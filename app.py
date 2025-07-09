from fastapi import FastAPI, HTTPException, Request, Body
from fastapi.responses import FileResponse, StreamingResponse
import pandas as pd
import os
import subprocess
import uuid
import numpy as np
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict
import os
import urllib.request
import gdown

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Content-Length", "Content-Type"]
)

TRIM_FOLDER = "trimmed_videos"
os.makedirs(TRIM_FOLDER, exist_ok=True)

def get_timestamps(csv_path: str, start_lat: float, start_lon: float, end_lat: float, end_lon: float) -> tuple:
    try:
        df = pd.read_csv(csv_path)
        df = df[df["lat"].notnull() & df["lon"].notnull()]

        df["distance_start"] = np.sqrt((df["lat"] - start_lat)**2 + (df["lon"] - start_lon)**2)
        df["distance_end"] = np.sqrt((df["lat"] - end_lat)**2 + (df["lon"] - end_lon)**2)

        start_row = df.loc[df["distance_start"].idxmin()]
        end_row = df.loc[df["distance_end"].idxmin()]

        start_ts = start_row.get("timestamp_sec")
        end_ts = end_row.get("timestamp_sec")

        if pd.isna(start_ts) or pd.isna(end_ts):
            return None, None

        return float(start_ts), float(end_ts)
    except Exception as e:
        print(f"Error processing timestamps: {str(e)}")
        return None, None


@app.on_event("startup")
def download_videos():
    def download_if_missing(gdrive_id, dest):
        if not os.path.exists(dest):
            print(f"Downloading {dest} from Google Drive...")
            gdown.download(id=gdrive_id, output=dest, quiet=False)

    L2_ID = "1B-SySPfrSL2lietk3dKoZetIyfxJ-wfv"
    R2_ID = "1cjWAkltEMEDP4x1hFm7gP0tVCw8l_Bi2"

    download_if_missing(L2_ID, "L2.mp4")
    download_if_missing(R2_ID, "R2.mp4")

@app.post("/trim")
async def trim_video(data: Dict = Body(...), request: Request = None):
    try:
        source = data.get("source")
        start_lat = data.get("start_lat")
        start_lon = data.get("start_lon")
        end_lat = data.get("end_lat")
        end_lon = data.get("end_lon")

        if source not in ["L2", "R2"]:
            raise HTTPException(status_code=400, detail="Invalid source video name")

        try:
            start_lat = float(start_lat)
            start_lon = float(start_lon)
            end_lat = float(end_lat)
            end_lon = float(end_lon)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid coordinate values")

        csv_path = "coordinates2.csv" if source == "R2" else "coordinates.csv"
        video_path = "R2.mp4" if source == "R2" else "L2.mp4"

        start_ts, end_ts = get_timestamps(csv_path, start_lat, start_lon, end_lat, end_lon)
        if start_ts is None or end_ts is None or start_ts >= end_ts:
            raise HTTPException(status_code=400, detail="Invalid coordinates or timestamps")

        output_filename = f"{uuid.uuid4().hex}.mp4"
        output_path = os.path.join(TRIM_FOLDER, output_filename)

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_ts),
            "-to", str(end_ts),
            "-i", video_path,
            "-c:v", "libx264", "-preset", "fast",  # Changed from copy to ensure proper encoding
            "-c:a", "aac",
            "-movflags", "frag_keyframe+empty_moov",  # Better for streaming
            "-f", "mp4",
            output_path
        ]

        try:
            subprocess.run(cmd, check=True, timeout=30)  # Added timeout
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=500, detail="Video processing timed out")
        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail=f"FFmpeg error: {str(e)}")

        # Return the URL with the correct scheme (http/https)
        base_url = str(request.base_url)
        if "cloudflare" in base_url:
            base_url = base_url.replace("http://", "https://")
        return {"video_url": f"{base_url}video/{output_filename}"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")

@app.get("/video/{filename}")
async def serve_video(filename: str, request: Request):
    file_path = os.path.join(TRIM_FOLDER, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video not found")
    
    file_size = os.path.getsize(file_path)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Content-Type": "video/mp4",
    }
    
    # Support for range requests (streaming)
    range_header = request.headers.get("Range")
    if range_header:
        from fastapi import Response
        start, end = range_header.replace("bytes=", "").split("-")
        start = int(start)
        end = int(end) if end else file_size - 1
        
        chunk_size = end - start + 1
        with open(file_path, "rb") as video_file:
            video_file.seek(start)
            data = video_file.read(chunk_size)
        
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        headers["Content-Length"] = str(chunk_size)
        
        return Response(
            content=data,
            status_code=206,
            headers=headers,
            media_type="video/mp4"
        )
    else:
        # Regular file response with streaming
        return FileResponse(
            file_path,
            headers=headers,
            media_type="video/mp4"
        )

@app.get("/trimmed/count")
def count_trimmed_videos():
    try:
        files = [f for f in os.listdir(TRIM_FOLDER) if f.endswith(".mp4")]
        return {"count": len(files)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error counting videos: {str(e)}")

@app.delete("/trimmed/delete-all")
def delete_all_trimmed_videos():
    try:
        deleted_files = []
        for f in os.listdir(TRIM_FOLDER):
            path = os.path.join(TRIM_FOLDER, f)
            if os.path.isfile(path):
                os.remove(path)
                deleted_files.append(f)
        return {"deleted": deleted_files, "message": f"Deleted {len(deleted_files)} videos"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting videos: {str(e)}")


