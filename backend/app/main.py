from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .database import engine, Base, run_lightweight_migrations
from .routers import ponds, batches, stocking, feeding, water_quality, medication, costs, harvest, analysis, reports

# 先对旧库做列级补齐,再建表,保证重启后幂等约束仍然成立
run_lightweight_migrations()
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="水产养殖管理系统",
    description="一个完整的水产养殖管理系统，支持塘口管理、投苗记录、日常管理、成本核算、出塘销售和养殖周期分析",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ponds.router)
app.include_router(batches.router)
app.include_router(reports.router)
app.include_router(stocking.router)
app.include_router(feeding.router)
app.include_router(water_quality.router)
app.include_router(medication.router)
app.include_router(costs.router)
app.include_router(harvest.router)
app.include_router(analysis.router)

@app.get("/")
def root():
    return {
        "message": "欢迎使用水产养殖管理系统API",
        "docs": "/docs",
        "version": "2.0.0"
    }

@app.get("/health")
def health_check():
    return {"status": "healthy"}
