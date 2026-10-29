# The TV Frame server: the photo pipeline, the photo scorer and the services, in one image.
# Built and started by ./tvframe setup (compose.yaml). The checkout itself is mounted at /tvframe, so the code
# and everything built from the library stay on the host, next to each other, as on a server without Docker.
#
#   TORCH=cu128  NVIDIA GPUs (the default; torch brings its own CUDA, the host needs only the driver)
#   TORCH=cpu    no NVIDIA GPU: scoring and encoding on the CPU, slower, the same results
FROM ubuntu:24.04

ARG TORCH=cu128
ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/venv/bin:$PATH \
    TVFRAME_IN_DOCKER=1 \
    TVFRAME_HOME=/tvframe \
    HOME=/tvframe/.home \
    TVF_MODELS=/opt/tvframe/models \
    HF_HOME=/opt/tvframe/hf \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv ffmpeg libimage-exiftool-perl adb ca-certificates curl tzdata \
        libglib2.0-0 fonts-dejavu-core procps \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv \
    && pip install --no-cache-dir torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/${TORCH} \
    && pip install --no-cache-dir torch==2.8.0 torchvision==0.23.0 open_clip_torch==2.32.0 \
        opencv-python-headless==4.11.0.86 pillow==12.0.0 pillow-heif==1.8.1 numpy==2.3.4 androidtvremote2==0.3.2

# The scorer's models: face detection (OpenCV Zoo, MIT), the LAION aesthetic head (MIT) and CLIP ViT-L/14
RUN mkdir -p $TVF_MODELS $HF_HOME \
    && curl -fsSL -o $TVF_MODELS/face_detection_yunet_2023mar.onnx \
        https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx \
    && curl -fsSL -o "$TVF_MODELS/sac+logos+ava1-l14-linearMSE.pth" \
        "https://github.com/christophschuhmann/improved-aesthetic-predictor/raw/main/sac+logos+ava1-l14-linearMSE.pth" \
    && python -c "import open_clip; open_clip.create_model_and_transforms('ViT-L-14', pretrained='openai')" \
    && chmod -R a+rwX /opt/tvframe

# the scorer writes photo paths as /photos/..., wherever it runs
RUN ln -s /tvframe/photos /photos
WORKDIR /tvframe
EXPOSE 8151 8153 8154
CMD ["python3", "tools/services.py"]
