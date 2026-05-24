from datetime import datetime, timezone
from time import time
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session, query
from src.database import Base, engine, get_db
from src.models import Node
from src.schemas import NodeCreate, NodeResponse, NodeUpdate
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, Gauge, generate_latest


Base.metadata.create_all(bind=engine)
app = FastAPI()

request_counter = Counter(
    "http_requests_total", 
    "Total counter of http requests",
    ['method', 'endpoint', 'status_code'],
)

request_duration = Histogram(
    "http_request_duration",
    "Latency of http requests",
    ['method', 'endpoint', 'status_code'],
)

active_nodes = Gauge(
    "active_nodes",
    "Number of active nodes",
)

@app.middleware("http")
async def metrics_mw(request: Request, call_next):
    start = time()
    response = None

    try:
        response = await call_next(request)
        return response

    finally: 
        duration = time() - start

        route = request.scope.get("route")
        endpoint = (
                route.path
                if route
                else request.url.path
            )

        excluded = {
            "/metrics",
            "/docs",
            "/openapi.json",
            "/redoc",
        }

        if endpoint not in excluded:
            status = (
                response.status_code
                if response
                else 500
            )

            request_counter.labels(
                method=request.method,
                endpoint=endpoint,
                status_code=status
            ).inc()

            request_duration.labels(
                method=request.method,
                endpoint=endpoint,
                status_code=status
            ).observe(duration)


@app.get("/metrics")
def get_metrics(db: Session = Depends(get_db)):
    try:
        count = db.query(Node).filter(Node.status == "active").count()
        active_nodes.set(count)
    except Exception:
        pass

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)



@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        db_status = "disconnected"
    count = db.query(Node).filter(Node.status == "active").count()
    return {"status": "ok", "db": db_status, "nodes_count": count}

@app.post("/api/nodes", response_model=NodeResponse, status_code=201)
def register_node(node: NodeCreate, db: Session = Depends(get_db)):
    existing = db.query(Node).filter(Node.name == node.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Node already exists")
    db_node = Node(name=node.name, host=node.host, port=node.port)
    db.add(db_node)
    db.commit()
    db.refresh(db_node)
    return db_node

@app.get("/api/nodes", response_model=list[NodeResponse])
def list_nodes(db: Session = Depends(get_db)):
    return db.query(Node).all()

@app.get("/api/nodes/{name}", response_model=NodeResponse)
def get_node(name: str, db: Session = Depends(get_db)):
    node = db.query(Node).filter(Node.name == name).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node

@app.put("/api/nodes/{name}", response_model=NodeResponse)
def update_node(name: str, update: NodeUpdate, db: Session = Depends(get_db)):
    node = db.query(Node).filter(Node.name == name).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if update.host is not None:
        node.host = update.host
    if update.port is not None:
        node.port = update.port
    node.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(node)
    return node

@app.delete("/api/nodes/{name}", status_code=204)
def delete_node(name: str, db: Session = Depends(get_db)):
    node = db.query(Node).filter(Node.name == name).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    node.status = "inactive"
    node.updated_at = datetime.now(timezone.utc)
    db.commit()
    return Response(status_code=204)
