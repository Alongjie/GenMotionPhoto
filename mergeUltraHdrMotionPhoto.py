import os
import subprocess
import logging
import tempfile
import shutil

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def create_combined_xmp(sdr_len, gm_len, video_len, params, tmp_dir):
    """
    生成同时包含 GainMap 和 MotionPhoto 描述的 XMP
    """
    # HDR 参数
    gm_min = params.get('gainMapMin', 0.0)
    gm_max = params.get('gainMapMax', 2.1)
    gamma = params.get('gamma', 1.0)
    h_max = params.get('hdrCapacityMax', 2.1)

    xmp_content = f'''<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 5.1.0">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:GCamera="http://ns.google.com/photos/1.0/camera/"
    xmlns:GContainer="http://ns.google.com/photos/1.0/container/"
    xmlns:Item="http://ns.google.com/photos/1.0/container/item/"
    xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/">
   <GCamera:MotionPhoto>1</GCamera:MotionPhoto>
   <GCamera:MotionPhotoVersion>1</GCamera:MotionPhotoVersion>
   <hdrgm:Version>1.0</hdrgm:Version>
   <hdrgm:GainMapMin>{gm_min}</hdrgm:GainMapMin>
   <hdrgm:GainMapMax>{gm_max}</hdrgm:GainMapMax>
   <hdrgm:Gamma>{gamma}</hdrgm:Gamma>
   <hdrgm:HDRCapacityMax>{h_max}</hdrgm:HDRCapacityMax>
   <hdrgm:BaseRendition>SDR</hdrgm:BaseRendition>
   <GContainer:Directory>
    <rdf:Seq>
     <rdf:li rdf:parseType="Resource">
      <Item:Mime>image/jpeg</Item:Mime>
      <Item:Semantic>Primary</Item:Semantic>
      <Item:Length>{sdr_len}</Item:Length>
     </rdf:li>
     <rdf:li rdf:parseType="Resource">
      <Item:Mime>image/jpeg</Item:Mime>
      <Item:Semantic>GainMap</Item:Semantic>
      <Item:Length>{gm_len}</Item:Length>
     </rdf:li>
     <rdf:li rdf:parseType="Resource">
      <Item:Mime>video/mp4</Item:Mime>
      <Item:Semantic>MotionPhoto</Item:Semantic>
      <Item:Length>{video_len}</Item:Length>
     </rdf:li>
    </rdf:Seq>
   </GContainer:Directory>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>'''
    
    xmp_path = os.path.join(tmp_dir, "combined.xmp")
    with open(xmp_path, "w", encoding="utf-8") as f:
        f.write(xmp_content.strip())
    return xmp_path

def apply_metadata(file_path, xmp_path):
    cmd = ["exiftool", "-overwrite_original", "-n", f"-xmp<={xmp_path}", "-MPFVersion=0100", "-NumberOfImages=3", file_path]
    subprocess.run(cmd, check=True, capture_output=True)

def gen_hdr_motion_photo(sdr_path, gm_path, video_path, output_path, params):
    gm_size = os.path.getsize(gm_path)
    video_size = os.path.getsize(video_path)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_jpg = os.path.join(tmp_dir, "base.jpg")
        shutil.copy2(sdr_path, tmp_jpg)

        logging.info("Step 1: 注入 HDR + Motion 联合元数据...")
        # 第一次写入占位
        xmp = create_combined_xmp(0, gm_size, video_size, params, tmp_dir)
        apply_metadata(tmp_jpg, xmp)
        
        # 第二次写入精确长度
        final_sdr_size = os.path.getsize(tmp_jpg)
        xmp_final = create_combined_xmp(final_sdr_size, gm_size, video_size, params, tmp_dir)
        apply_metadata(tmp_jpg, xmp_final)

        if os.path.exists(output_path): os.remove(output_path)
        shutil.move(tmp_jpg, output_path)

    logging.info("Step 2: 正在物理拼接 GainMap 和 Video 流...")
    with open(output_path, "ab") as f_dst:
        with open(gm_path, "rb") as f_gm: f_dst.write(f_gm.read())
        with open(video_path, "rb") as f_vid: f_dst.write(f_vid.read())

    logging.info(f"✨ 合成成功！{output_path}")

# --- 测试运行 ---
if __name__ == "__main__":
    PARAMS = {'gainMapMax': 2.1, 'gamma': 1.0, 'hdrCapacityMax': 2.1}
    try:
        gen_hdr_motion_photo("test/sdr.jpg", "test/gainmap.jpg", "test/video.mp4", "output/FULL_HDR_MOTION.jpg", PARAMS)
    except Exception as e:
        logging.error(f"失败: {e}")
