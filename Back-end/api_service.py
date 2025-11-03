"""
FastAPI Backend for Timetable Scheduler

Installation:
pip install fastapi uvicorn ortools pandas openpyxl python-multipart

Run server:
uvicorn api_server:app --reload --host 0.0.0.0 --port 8000

Test:
curl -X POST http://localhost:8000/api/schedule -H "Content-Type: application/json" -d @input.json
"""

from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional
import json
import pandas as pd
from io import BytesIO
import os

# Import the scheduler
from scheduler import schedule_timetable, ScheduledSession

app = FastAPI(
    title="University Timetable Scheduler API",
    description="Automatic timetable generation using constraint programming",
    version="1.0.0"
)

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== REQUEST MODELS ====================

class ScheduleRequest(BaseModel):
    data: Dict
    max_time_seconds: Optional[int] = 300
    soft_constraints: Optional[List[str]] = []

class ScheduleResponse(BaseModel):
    status: str
    message: str
    solve_time: float
    total_sessions: Optional[int] = None
    schedule: Optional[List[Dict]] = None
    violations: Optional[List[str]] = []

# ==================== API ENDPOINTS ====================

@app.get("/")
def root():
    return {
        "service": "University Timetable Scheduler",
        "version": "1.0.0",
        "status": "running"
    }

@app.post("/api/schedule", response_model=ScheduleResponse)
async def create_schedule(request: ScheduleRequest):
    """
    Generate a timetable schedule

    Body:
    {
        "data": {
            "rooms": [...],
            "instructors": [...],
            "groups": [...],
            "sections": [...],
            "courses": [...]
        },
        "max_time_seconds": 300,
        "soft_constraints": ["minimize_gaps", "balance_load"]
    }
    """
    try:
        # Validate input
        required_keys = ['rooms', 'instructors', 'groups', 'sections', 'courses']
        for key in required_keys:
            if key not in request.data:
                raise HTTPException(
                    status_code=400,
                    detail=f"Missing required field: {key}"
                )

        # Run scheduler
        result = schedule_timetable(
            request.data,
            max_time_seconds=request.max_time_seconds
        )

        # Convert schedule to dict format
        if 'schedule' in result and result['schedule']:
            result['schedule'] = [
                {
                    'course_id': s.course_id,
                    'session_type': s.session_type,
                    'group_id': s.group_id,
                    'sections': s.sections,
                    'instructor_id': s.instructor_id,
                    'room_id': s.room_id,
                    'day': s.day,
                    'start_period': s.start_period,
                    'duration_periods': s.duration_periods
                }
                for s in result['schedule']
            ]

        return result

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Scheduling error: {str(e)}"
        )

@app.post("/api/schedule/file")
async def create_schedule_from_file(file: UploadFile = File(...)):
    """
    Generate schedule from uploaded JSON file
    """
    try:
        contents = await file.read()
        data = json.loads(contents)

        result = schedule_timetable(data, max_time_seconds=300)

        # Convert to dict
        if 'schedule' in result and result['schedule']:
            result['schedule'] = [
                {
                    'course_id': s.course_id,
                    'session_type': s.session_type,
                    'group_id': s.group_id,
                    'sections': s.sections,
                    'instructor_id': s.instructor_id,
                    'room_id': s.room_id,
                    'day': s.day,
                    'start_period': s.start_period,
                    'duration_periods': s.duration_periods
                }
                for s in result['schedule']
            ]

        return result

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/schedule/export/excel")
async def export_schedule_excel(request: ScheduleRequest):
    """
    Generate schedule and export as Excel file
    """
    try:
        result = schedule_timetable(request.data, max_time_seconds=request.max_time_seconds)

        if result['status'] not in ['success', 'feasible']:
            raise HTTPException(status_code=500, detail=result['message'])

        # Convert to DataFrame
        schedule_data = []
        day_names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday']

        for session in result['schedule']:
            schedule_data.append({
                'Course': session.course_id,
                'Type': session.session_type,
                'Group': session.group_id,
                'Sections': ', '.join(session.sections),
                'Instructor': session.instructor_id,
                'Room': session.room_id,
                'Day': day_names[session.day],
                'Start Period': session.start_period,
                'Duration (periods)': session.duration_periods,
                'Start Time': f"{8 + session.start_period * 0.75:.0f}:{(session.start_period * 45 % 60):02.0f}",
            })

        df = pd.DataFrame(schedule_data)

        # Create Excel file in memory
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Schedule', index=False)

            # Create additional views
            # By Group
            for group_id in df['Group'].unique():
                group_df = df[df['Group'] == group_id].sort_values(['Day', 'Start Period'])
                group_df.to_excel(writer, sheet_name=f'Group_{group_id}', index=False)

        output.seek(0)

        # Save temporarily and return
        filename = 'timetable_schedule.xlsx'
        with open(filename, 'wb') as f:
            f.write(output.getvalue())

        return FileResponse(
            filename,
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            filename=filename
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/health")
def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "scheduler"}

@app.get("/api/stats")
def get_stats(data: Dict):
    """Get statistics about the scheduling problem"""
    try:
        stats = {
            "rooms": len(data.get('rooms', [])),
            "instructors": len(data.get('instructors', [])),
            "groups": len(data.get('groups', [])),
            "sections": len(data.get('sections', [])),
            "courses": len(data.get('courses', [])),
            "room_types": {},
            "instructor_roles": {}
        }

        # Room type breakdown
        for room in data.get('rooms', []):
            room_type = room.get('type', 'unknown')
            stats['room_types'][room_type] = stats['room_types'].get(room_type, 0) + 1

        # Instructor role breakdown
        for instr in data.get('instructors', []):
            role = instr.get('role', 'unknown')
            stats['instructor_roles'][role] = stats['instructor_roles'].get(role, 0) + 1

        return stats

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ==================== ERROR HANDLERS ====================

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": str(exc),
            "detail": "An unexpected error occurred"
        }
    )

# ==================== STARTUP ====================

@app.on_event("startup")
async def startup_event():
    print("=" * 60)
    print("University Timetable Scheduler API")
    print("=" * 60)
    print("Server started successfully!")
    print("\nAvailable endpoints:")
    print("  POST /api/schedule                 - Generate schedule from JSON")
    print("  POST /api/schedule/file            - Generate schedule from file upload")
    print("  POST /api/schedule/export/excel    - Generate & export as Excel")
    print("  GET  /api/health                   - Health check")
    print("  GET  /                             - API info")
    print("\nDocs available at: http://localhost:8000/docs")
    print("=" * 60)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)