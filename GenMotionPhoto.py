import os
import subprocess
import logging
import tempfile
import shutil

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)


def create_xmp_file(image_length, video_length, tmp_dir):
    xmp_content = f'''<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 5.1.0">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:GCamera="http://ns.google.com/photos/1.0/camera/"
    xmlns:GContainer="http://ns.google.com/photos/1.0/container/"
    xmlns:Item="http://ns.google.com/photos/1.0/container/item/">
   <GCamera:MotionPhoto>1</GCamera:MotionPhoto>
   <GCamera:MotionPhotoVersion>1</GCamera:MotionPhotoVersion>
   <GCamera:MotionPhotoPresentationTimestampUs>0</GCamera:MotionPhotoPresentationTimestampUs>
   <GContainer:Directory>
    <rdf:Seq>
     <rdf:li rdf:parseType="Resource">
      <Item:Mime>image/jpeg</Item:Mime>
      <Item:Semantic>Primary</Item:Semantic>
      <Item:Length>{image_length}</Item:Length>
      <Item:Padding>0</Item:Padding>
     </rdf:li>
     <rdf:li rdf:parseType="Resource">
      <Item:Mime>video/mp4</Item:Mime>
      <Item:Semantic>MotionPhoto</Item:Semantic>
      <Item:Length>{video_length}</Item:Length>
      <Item:Padding>0</Item:Padding>
     </rdf:li>
    </rdf:Seq>
   </GContainer:Directory>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>'''

    xmp_path = os.path.join(tmp_dir, "motion.xmp")
    with open(xmp_path, "w", encoding="utf-8") as f:
        f.write(xmp_content.strip())
    return xmp_path


def apply_metadata(file_path, xmp_path, exiftool_path="exiftool"):
    cmd = [
        exiftool_path,
        "-overwrite_original",
        "-n",
        f"-xmp<={xmp_path}",
        file_path
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        logging.error("ExifTool 写入失败！")
        logging.error(f"错误输出: {e.stderr}")
        raise

def gen_motion_photo(photo_path, video_path, output_dir):
    abs_photo = os.path.abspath(photo_path)
    abs_video = os.path.abspath(video_path)
    file_name = os.path.basename(photo_path)
    output_path = os.path.abspath(os.path.join(output_dir, file_name))
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(abs_video):
        raise FileNotFoundError(f"找不到视频文件: {abs_video}")
    video_size = os.path.getsize(abs_video)

    # 使用临时目录处理，防止 ExifTool 扫描到已损坏的旧输出文件
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_photo = os.path.join(tmp_dir, "temp_image.jpg")
        shutil.copy2(abs_photo, tmp_photo)

        logging.info(f"第一步: 正在为纯净图片注入元数据...")

        # 1. 第一次写入元数据：建立 XMP 结构
        current_size = os.path.getsize(tmp_photo)
        xmp_file = create_xmp_file(current_size, video_size, tmp_dir)
        apply_metadata(tmp_photo, xmp_file)

        # 2. 第二次写入元数据：修正因为 XMP 注入导致的文件大小偏移
        # Google 相册要求 Offset 必须极其精确
        final_image_size = os.path.getsize(tmp_photo)
        xmp_file_final = create_xmp_file(final_image_size, video_size, tmp_dir)
        apply_metadata(tmp_photo, xmp_file_final)

        # 3. 将处理好的图片移动到最终输出位置
        if os.path.exists(output_path):
            os.remove(output_path)
        shutil.move(tmp_photo, output_path)

        final_offset = os.path.getsize(output_path)
        logging.info(f"第二步: 元数据注入成功。图片偏移量: {final_offset} 字节")

    # 4. 追加视频二进制流
    logging.info(f"第三步: 正在追加视频流...")
    with open(abs_video, "rb") as f_src:
        video_data = f_src.read()

    with open(output_path, "ab") as f_dst:
        f_dst.write(video_data)

    logging.info(f"✨ 全部完成！文件已生成: {output_path}")

if __name__ == "__main__":
    SOURCE_JPG = "res/IMG_0001.jpg"
    SOURCE_MOV = "res/IMG_0001.MOV"
    OUTPUT_FOLDER = "output"

    try:
        gen_motion_photo(SOURCE_JPG, SOURCE_MOV, OUTPUT_FOLDER)
    except Exception as e:
        logging.error(f"程序运行中断: {e}")
