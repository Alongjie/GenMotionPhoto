import os
import subprocess
import logging
import tempfile
import shutil

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


def create_ultrahdr_xmp(image_length, gainmap_length, params, tmp_dir):
    """
    生成符合 Adobe Gain Map 规范的 XMP
    params 包含: gainMapMin, gainMapMax, gamma, hdrCapacityMin, hdrCapacityMax
    """
    # 提取参数
    gm_min = params.get('gainMapMin', 0.0)
    gm_max = params.get('gainMapMax', 1.0)
    gamma = params.get('gamma', 1.0)
    h_min = params.get('hdrCapacityMin', 0.0)
    h_max = params.get('hdrCapacityMax', 2.0)

    xmp_content = f'''<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 5.1.0">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:GContainer="http://ns.google.com/photos/1.0/container/"
    xmlns:Item="http://ns.google.com/photos/1.0/container/item/"
    xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/">
   <hdrgm:Version>1.0</hdrgm:Version>
   <hdrgm:GainMapMin>{gm_min}</hdrgm:GainMapMin>
   <hdrgm:GainMapMax>{gm_max}</hdrgm:GainMapMax>
   <hdrgm:Gamma>{gamma}</hdrgm:Gamma>
   <hdrgm:OffsetSDR>0.015625</hdrgm:OffsetSDR>
   <hdrgm:OffsetHDR>0.015625</hdrgm:OffsetHDR>
   <hdrgm:HDRCapacityMin>{h_min}</hdrgm:HDRCapacityMin>
   <hdrgm:HDRCapacityMax>{h_max}</hdrgm:HDRCapacityMax>
   <hdrgm:BaseRendition>SDR</hdrgm:BaseRendition>
   <GContainer:Directory>
    <rdf:Seq>
     <rdf:li rdf:parseType="Resource">
      <Item:Mime>image/jpeg</Item:Mime>
      <Item:Semantic>Primary</Item:Semantic>
      <Item:Length>0</Item:Length>
      <Item:Padding>0</Item:Padding>
     </rdf:li>
     <rdf:li rdf:parseType="Resource">
      <Item:Mime>image/jpeg</Item:Mime>
      <Item:Semantic>GainMap</Item:Semantic>
      <Item:Length>{gainmap_length}</Item:Length>
      <Item:Padding>0</Item:Padding>
     </rdf:li>
    </rdf:Seq>
   </GContainer:Directory>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>'''

    xmp_path = os.path.join(tmp_dir, "ultrahdr.xmp")
    with open(xmp_path, "w", encoding="utf-8") as f:
        f.write(xmp_content.strip())
    return xmp_path


def apply_ultrahdr_metadata(file_path, xmp_path):
    """
    使用 ExifTool 写入 XMP 并尝试构建 MPF 结构
    """
    cmd = [
        "exiftool",
        "-overwrite_original",
        f"-xmp<={xmp_path}",
        # 这一行告诉查看器后面还有一个 MPF 图像对象
        "-MPFVersion=0100",
        "-NumberOfImages=2",
        file_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def gen_ultra_hdr(sdr_path, gainmap_path, output_path, params):
    abs_sdr = os.path.abspath(sdr_path)
    abs_gm = os.path.abspath(gainmap_path)

    # 1. 检查文件合法性
    for p in [abs_sdr, abs_gm]:
        if not os.path.exists(p):
            raise FileNotFoundError(f"找不到文件: {p}")
        with open(p, 'rb') as f:
            if f.read(2) != b'\xff\xd8':
                raise ValueError(f"文件不是有效的 JPEG: {p}")

    gm_size = os.path.getsize(abs_gm)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_output = os.path.join(tmp_dir, "hdr_base.jpg")
        shutil.copy2(abs_sdr, tmp_output)  # 这里 photo 指代你的 sdr_path

        # 2. 处理元数据
        logging.info("正在注入 Ultra HDR 元数据...")
        # 第一次注入以确定大小
        xmp_file = create_ultrahdr_xmp(0, gm_size, params, tmp_dir)
        apply_ultrahdr_metadata(tmp_output, xmp_file)

        # 修正真正的 Primary 长度（即包含所有元数据的 SDR 部分大小）
        sdr_final_size = os.path.getsize(tmp_output)
        xmp_file_final = create_ultrahdr_xmp(sdr_final_size, gm_size, params, tmp_dir)
        apply_ultrahdr_metadata(tmp_output, xmp_file_final)

        # 3. 最终移动
        if os.path.exists(output_path):
            os.remove(output_path)
        shutil.move(tmp_output, output_path)

    # 4. 追加 Gain Map 二进制流
    logging.info(f"正在拼接 Gain Map (大小: {gm_size})...")
    with open(abs_gm, "rb") as f_gm:
        gm_data = f_gm.read()

    with open(output_path, "ab") as f_dst:
        f_dst.write(gm_data)

    logging.info(f"✨ Ultra HDR 生成成功: {output_path}")


# --- 配置参数 ---
if __name__ == "__main__":
    # 根据你的具体数据填写
    MY_PARAMS = {
        'gainMapMin': 0.0,  # 对应底噪
        'gainMapMax': 2.0,  # 对应高光增强倍率 (log2 空间)
        'gamma': 1.0,  # 增益图的纠正系数
        'hdrCapacityMin': 0.0,  # SDR 显示器的起点
        'hdrCapacityMax': 2.0  # 理想 HDR 显示器的峰值
    }

    try:
        gen_ultra_hdr("test/sdr.jpg", "test/ngm.jpg", "output1/ULTRA_HDR.jpg", MY_PARAMS)
    except Exception as e:
        logging.error(f"失败: {e}")
