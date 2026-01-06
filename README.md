# GenMotionPhoto
输入jpg和一段video合成一个MotionPhoto图像，参考“[Google1.0标准](https://developer.android.com/media/platform/motion-photo-format)”


## 合成原理
不解码直接，字节流拼接
JPEG图像  +  Video   +  XMP嵌入  =  MotionPhoto1.0

其中XMP嵌入信息（参考Google标准）：
Camera Metadata：
  Camera:MotionPhoto                          0/1（是否是动态照片的标志位）
  Camera:MotionPhotoVersion                   1
  Camera:MotionPhotoPresentationTimestampUs   视频与图像对应的帧，以微妙定位，-1未指定，或者默认短视频中间时间戳
  
Container element：



# GenUltraHDR Photo
输入jpg和Gainmap合成一个ultra hdr图像，参考"[Google ultraHdr标准](https://developer.android.com/media/platform/hdr-image-format?hl=zh-cn)"

## 合成原理
不解码，字节流拼接
JPEG图像  +  Gainmap  + XMP嵌入  =  UltraHdr

其中XMP嵌入信息（参考Adobe标准）：
Camera

