#!/usr/bin/env python3
"""用本机 ComfyUI 里的 MiniMax H3（首尾帧版）把一张参考图做成首尾相接的循环视频。

首帧和尾帧用同一张图，模型负责中间的运动，所以成片可以无缝循环播放。

用法：
  python3 -I gen_video.py ref.png out.mp4 --prompt-file p.txt [--seconds 10] [--seed 1]
"""
import argparse
import json
import math
import time
import urllib.request
import uuid
from pathlib import Path

HOST = "http://127.0.0.1:8188"
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(path, data=None, headers=None):
    req = urllib.request.Request(HOST + path, data=data, headers=headers or {})
    with NO_PROXY.open(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def upload(img: Path) -> str:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{img.name}\"\r\n"
            "Content-Type: image/png\r\n\r\n").encode() + img.read_bytes() + \
           f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n".encode()
    res = call("/upload/image", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return res["name"]


def canvas(w, h):
    """和节点里一样：短边 768、面积上限 768×1344、按 32 取整。"""
    r = w / h
    nw, nh = (768 * r, 768) if r >= 1 else (768, 768 / r)
    if nw * nh > 768 * 1344:
        s = math.sqrt(768 * 1344 / (nw * nh))
        nw, nh = nw * s, nh * s
    return round(nw / 32) * 32, round(nh / 32) * 32


def frames(seconds):
    n = max(5, round(seconds * 24))
    return n + (5 - n % 17) % 17


def graph(image_name, prompt, w, h, length, seed, prefix):
    return {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "type": "minimax", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "cond": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["clip", 0], "vae": ["vae", 0], "prompt": prompt,
            "width": w, "height": h, "length": length,
            "first_frame": ["img", 0], "last_frame": ["img", 0]}},
        "guider": {"class_type": "BasicGuider", "inputs": {"model": ["unet", 0], "conditioning": ["cond", 0]}},
        "sampler": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "sigmas": {"class_type": "BasicScheduler", "inputs": {"model": ["unet", 0], "scheduler": "simple", "steps": 20, "denoise": 1.0}},
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "sample": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["sampler", 0],
            "sigmas": ["sigmas", 0], "latent_image": ["cond", 1]}},
        "split": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["sample", 0]}},
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["split", 0], "vae": ["vae", 0]}},
        "video": {"class_type": "CreateVideo", "inputs": {"images": ["decode", 0], "fps": 24}},
        "save": {"class_type": "SaveVideo", "inputs": {"video": ["video", 0], "filename_prefix": prefix, "format": "mp4", "codec": "h264"}},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--prompt-file", type=Path, required=True)
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()

    from PIL import Image
    w, h = canvas(*Image.open(a.ref).size)
    length = frames(a.seconds)
    name = upload(a.ref)
    prefix = f"ryuo/{a.out.stem}"
    pid = call("/prompt", json.dumps({"prompt": graph(name, a.prompt_file.read_text(), w, h, length, a.seed, prefix)}).encode(),
               {"Content-Type": "application/json"})["prompt_id"]
    print(f"已提交 {pid}：{w}×{h}，{length} 帧", flush=True)
    t0 = time.time()
    while True:
        hist = call(f"/history/{pid}")
        if hist and pid in hist:
            st = hist[pid]["status"]
            if st.get("status_str") == "error":
                raise SystemExit("生成失败：" + json.dumps(st.get("messages"), ensure_ascii=False)[-1500:])
            if st.get("completed"):
                break
        time.sleep(10)
    outs = [v for o in hist[pid]["outputs"].values() for k in ("images", "videos", "gifs") for v in o.get(k, [])]
    src = Path.home() / "ComfyUI-Shared/ComfyUI/output" / outs[0].get("subfolder", "") / outs[0]["filename"]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(src.read_bytes())
    print(f"完成，用时 {round(time.time() - t0)} 秒 → {a.out}")


if __name__ == "__main__":
    main()
