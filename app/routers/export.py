import json

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response

from app.alerts.file_exporter import get_today_export_path

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


@router.get("/signals.xlsx", summary="Full signals + paper trades Excel workbook")
async def export_xlsx():
    """Download a formatted Excel workbook with all signals, gate details,
    paper trades, and P&L summary across all available dates."""
    import asyncio
    from app.alerts.xlsx_exporter import generate_xlsx
    data = await asyncio.to_thread(generate_xlsx)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=nse_fno_signals.xlsx"},
    )
