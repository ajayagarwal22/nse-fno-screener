from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from app.alerts.file_exporter import get_today_export_path
import json

router = APIRouter(prefix="/export", tags=["export"])


@router.get("/signals.csv")
async def export_csv():
    path = get_today_export_path("csv")
    if not path.exists():
        return JSONResponse({"detail": "No signals exported today yet"}, status_code=404)
    return FileResponse(path, media_type="text/csv", filename=path.name)


@router.get("/signals.json")
async def export_json():
    path = get_today_export_path("json")
    if not path.exists():
        return JSONResponse({"detail": "No signals exported today yet"}, status_code=404)
    with open(path) as f:
        data = json.load(f)
    return data
