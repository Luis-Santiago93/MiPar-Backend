import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import httpx
from fastapi import Cookie, Depends, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from pwdlib import PasswordHash
from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, func, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from vercel.blob import AsyncBlobClient
from app.database import database_engine


engine = database_engine()
SessionLocal = sessionmaker(engine, expire_on_commit=False)
password_hash = PasswordHash.recommended()


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    category: Mapped[str] = mapped_column(String(100))
    price: Mapped[float] = mapped_column(Float)
    sale_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    description: Mapped[str] = mapped_column(String(1000), default="")
    colors: Mapped[list] = mapped_column(JSON)
    variants: Mapped[list] = mapped_column(JSON)
    image: Mapped[str] = mapped_column(String(2000))
    images: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tone: Mapped[str] = mapped_column(String(30), default="sand")
    badge: Mapped[str | None] = mapped_column(String(60), nullable=True)
    request_only: Mapped[bool] = mapped_column(Boolean, default=False)


class Zone(Base):
    __tablename__ = "zones"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    fee: Mapped[float] = mapped_column(Float, default=0)
    weekdays: Mapped[list] = mapped_column(JSON)
    delivery_times: Mapped[list] = mapped_column(JSON)
    available: Mapped[bool] = mapped_column(Boolean, default=True)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    customer: Mapped[dict] = mapped_column(JSON)
    items: Mapped[list] = mapped_column(JSON)
    lines: Mapped[list] = mapped_column(JSON)
    zone_id: Mapped[str] = mapped_column(String(60))
    zone_name: Mapped[str] = mapped_column(String(120))
    delivery_date: Mapped[str] = mapped_column(String(10))
    delivery_time: Mapped[str] = mapped_column(String(5))
    payment_method: Mapped[str] = mapped_column(String(20), default="efectivo")
    subtotal: Mapped[float] = mapped_column(Float)
    delivery_fee: Mapped[float] = mapped_column(Float)
    total: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="nuevo")


class ProductRequest(Base):
    __tablename__ = "product_requests"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    product_id: Mapped[str] = mapped_column(String(60))
    product_name: Mapped[str] = mapped_column(String(150))
    color: Mapped[str] = mapped_column(String(80))
    size: Mapped[float] = mapped_column(Float)
    customer: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="nueva")


class Payload(BaseModel):
    model_config = {"extra": "allow"}


def db_session():
    with SessionLocal() as session:
        yield session


def product_json(p: Product) -> dict:
    return {"id": p.id, "name": p.name, "category": p.category, "price": p.price, "salePrice": p.sale_price, "description": p.description, "colors": p.colors, "variants": p.variants, "image": p.image, "images": p.images or [p.image], "tone": p.tone, "badge": p.badge, "requestOnly": p.request_only}


def zone_json(z: Zone, public: bool = False) -> dict:
    data = {"id": z.id, "name": z.name, "fee": z.fee, "weekdays": z.weekdays, "deliveryTimes": z.delivery_times, "available": z.available}
    if public:
        data["dates"] = next_dates(z.weekdays)
    return data


def order_json(o: Order) -> dict:
    return {"id": o.id, "createdAt": o.created_at.isoformat(), "soldAt": o.sold_at.isoformat() if o.sold_at else None, "customer": o.customer, "items": o.items, "lines": o.lines, "zoneId": o.zone_id, "zoneName": o.zone_name, "deliveryDate": o.delivery_date, "deliveryTime": o.delivery_time, "paymentMethod": o.payment_method, "subtotal": o.subtotal, "deliveryFee": o.delivery_fee, "total": o.total, "currency": "MXN", "status": o.status, "simulated": False}


def request_json(r: ProductRequest) -> dict:
    return {"id": r.id, "createdAt": r.created_at.isoformat(), "productId": r.product_id, "productName": r.product_name, "color": r.color, "size": r.size, "customer": r.customer, "status": r.status}


def next_dates(weekdays: list[int]) -> list[dict]:
    labels = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"]
    result, day, attempts = [], datetime.now(), 0
    while len(result) < 3 and attempts < 21:
        day += timedelta(days=1)
        attempts += 1
        js_weekday = (day.weekday() + 1) % 7
        if js_weekday in weekdays:
            result.append({"date": day.strftime("%Y-%m-%d"), "label": f"{labels[js_weekday]} {day.day}"})
    return result


def normalize_phone(value: str) -> str:
    digits = "".join(c for c in value if c.isdigit())
    return digits[2:] if len(digits) == 12 and digits.startswith("52") else digits


def quote(data: dict, db: Session) -> tuple[dict, Zone]:
    items = data.get("items") or []
    zone = db.get(Zone, data.get("zoneId"))
    if not items or not zone or not zone.available:
        raise HTTPException(400, "Agrega productos y selecciona una zona disponible")
    if data.get("deliveryDate") not in [d["date"] for d in next_dates(zone.weekdays)] or data.get("deliveryTime") not in zone.delivery_times:
        raise HTTPException(400, "Fecha u hora de entrega no disponible")
    subtotal, requested = 0.0, {}
    for item in items:
        product = db.get(Product, item.get("productId"))
        variant = next((v for v in (product.variants if product else []) if v["color"] == item.get("color") and v["size"] == item.get("size")), None)
        quantity = item.get("quantity", 0)
        if not product or product.request_only or not variant or not isinstance(quantity, int) or quantity < 1:
            raise HTTPException(400, "Producto o variante inválida")
        key = f'{product.id}:{variant["color"]}:{variant["size"]}'
        requested[key] = requested.get(key, 0) + quantity
        if requested[key] > variant["stock"]:
            raise HTTPException(400, "Existencias insuficientes")
        subtotal += (product.sale_price or product.price) * quantity
    return {"subtotal": subtotal, "deliveryFee": zone.fee, "total": subtotal + zone.fee, "currency": "MXN", "deliveryTime": data["deliveryTime"], "simulated": False}, zone


def admin_session(mipar_admin: str | None = Cookie(default=None)) -> str:
    if not mipar_admin:
        raise HTTPException(401, "Inicia sesión para entrar al backoffice")
    try:
        return jwt.decode(mipar_admin, os.getenv("JWT_SECRET", "change-me-in-production"), algorithms=["HS256"])["sub"]
    except Exception as exc:
        raise HTTPException(401, "Sesión inválida") from exc


def seed(db: Session) -> None:
    # An empty catalog can be intentional; existing zones mean setup already ran.
    if db.scalar(select(Product.id).limit(1)) is not None or db.scalar(select(Zone.id).limit(1)) is not None:
        return
    variants = lambda color, stocks: [{"color": color, "size": size, "stock": stock} for size, stock in stocks]
    db.add_all([
        Product(id="urbano-uno", name="Urbano Uno", category="Tenis casuales", price=499, description="Un par cómodo para todos los días.", colors=["Marfil", "Negro"], variants=variants("Marfil", [(24,2),(25,3),(26,1),(27,2),(28,0)]) + variants("Negro", [(24,1),(25,0),(26,3),(27,1),(28,2)]), image="/images/urbano-uno.png", images=["/images/urbano-uno.png"], tone="sand", badge="Favorito"),
        Product(id="ruta-nueva", name="Ruta Nueva", category="Tenis casuales", price=0, description="Modelo disponible para solicitar.", colors=["Blanco", "Negro"], variants=variants("Blanco", [(24,0),(25,0),(26,0),(27,0),(28,0)]) + variants("Negro", [(24,0),(25,0),(26,0),(27,0),(28,0)]), image="/images/urbano-uno.png", images=["/images/urbano-uno.png"], tone="lilac", badge="Solo solicitud", request_only=True),
        Zone(id="centro", name="Zona Centro", fee=0, weekdays=[1,3,5], delivery_times=["10:00","16:00"], available=True),
        Zone(id="norte", name="Zona Norte", fee=30, weekdays=[2,4], delivery_times=["12:00"], available=True),
    ])
    db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("products")}
        if "images" not in columns:
            connection.execute(text("ALTER TABLE products ADD COLUMN images JSON"))
        if "sale_price" not in columns:
            connection.execute(text("ALTER TABLE products ADD COLUMN sale_price FLOAT"))
        order_columns = {column["name"] for column in inspect(connection).get_columns("orders")}
        if "payment_method" not in order_columns:
            connection.execute(text("ALTER TABLE orders ADD COLUMN payment_method VARCHAR(20) NOT NULL DEFAULT 'efectivo'"))
        request_columns = {column["name"]: column for column in inspect(connection).get_columns("product_requests")}
        if engine.dialect.name == "postgresql" and not isinstance(request_columns["size"]["type"], Float):
            connection.execute(text("ALTER TABLE product_requests ALTER COLUMN size TYPE DOUBLE PRECISION USING size::double precision"))
    with SessionLocal() as db:
        seed(db)
    yield


app = FastAPI(title="MiPar API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("FRONTEND_URL", "http://localhost:3000")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/")
def root(): return RedirectResponse(url="/docs", status_code=307)


@app.get("/api/health")
def health(): return {"status": "ok", "service": "MiPar API", "environment": os.getenv("ENVIRONMENT", "development")}


@app.get("/api/products")
def products(db: Session = Depends(db_session)): return [product_json(p) for p in db.scalars(select(Product)).all()]


@app.get("/api/products/{slug}")
def product(slug: str, db: Session = Depends(db_session)):
    item = db.get(Product, slug)
    if not item: raise HTTPException(404, "Producto no encontrado")
    return product_json(item)


@app.get("/api/delivery/zones")
def delivery_zones(db: Session = Depends(db_session)): return [zone_json(z, True) for z in db.scalars(select(Zone)).all()]


@app.get("/api/location/reverse")
def reverse_location(lat: float, lon: float):
    try:
        response = httpx.get("https://nominatim.openstreetmap.org/reverse", params={"lat": lat, "lon": lon, "format": "jsonv2", "accept-language": "es"}, headers={"User-Agent": "MiPar/1.0"}, timeout=8)
        response.raise_for_status()
        data = response.json()
        return {"displayName": data.get("display_name", ""), "address": data.get("display_name", "")}
    except httpx.HTTPError as exc:
        raise HTTPException(502, "No fue posible obtener la dirección") from exc


@app.post("/api/orders/quote")
def order_quote(body: Payload, db: Session = Depends(db_session)): return quote(body.model_dump(), db)[0]


@app.post("/api/orders", status_code=201)
def create_order(body: Payload, db: Session = Depends(db_session)):
    data = body.model_dump(); customer = data.get("customer") or {}; phone = normalize_phone(customer.get("phone", ""))
    if len(customer.get("name", "").strip()) < 2 or len(phone) != 10 or len(customer.get("address", "").strip()) < 8: raise HTTPException(400, "Completa nombre, teléfono de 10 dígitos y dirección")
    payment_method = data.get("paymentMethod")
    if payment_method not in ["efectivo", "transferencia", "tarjeta"]: raise HTTPException(400, "Selecciona un método de pago")
    totals, zone = quote(data, db); lines = []
    for item in data["items"]:
        p = db.get(Product, item["productId"]); variants = [dict(v) for v in p.variants]; v = next(v for v in variants if v["color"] == item["color"] and v["size"] == item["size"]); v["stock"] -= item["quantity"]; p.variants = variants
        lines.append({"productId": p.id, "name": p.name, "color": item["color"], "size": item["size"], "quantity": item["quantity"], "unitPrice": p.sale_price or p.price, "lineTotal": (p.sale_price or p.price) * item["quantity"]})
    now = datetime.now(timezone.utc); consecutive = (db.scalar(select(func.count(Order.id))) or 0) + 1; order = Order(id=f"JIREH-{int(now.timestamp()):X}-{consecutive:04d}", created_at=now, customer={**customer, "phone": phone}, items=data["items"], lines=lines, zone_id=zone.id, zone_name=zone.name, delivery_date=data["deliveryDate"], delivery_time=data["deliveryTime"], payment_method=payment_method, subtotal=totals["subtotal"], delivery_fee=totals["deliveryFee"], total=totals["total"], status="nuevo")
    db.add(order); db.commit(); return order_json(order)


@app.post("/api/product-requests", status_code=201)
def create_request(body: Payload, db: Session = Depends(db_session)):
    data = body.model_dump(); p = db.get(Product, data.get("productId")); v = next((v for v in (p.variants if p else []) if v["color"] == data.get("color") and v["size"] == data.get("size")), None); customer = data.get("customer") or {}; phone = normalize_phone(customer.get("phone", ""))
    if not p or not v or (not p.request_only and v["stock"] > 0): raise HTTPException(400, "Este modelo está disponible para compra o la variante no existe")
    if len(customer.get("name", "").strip()) < 2 or len(phone) != 10: raise HTTPException(400, "Escribe tu nombre y teléfono de 10 dígitos")
    now = datetime.now(timezone.utc); req = ProductRequest(id=f"SOL-{int(now.timestamp()):X}-{secrets.token_hex(2).upper()}", created_at=now, product_id=p.id, product_name=p.name, color=v["color"], size=v["size"], customer={"name": customer["name"].strip(), "phone": phone, "notes": customer.get("notes", "").strip()}, status="nueva")
    db.add(req); db.commit(); return request_json(req)


@app.post("/api/admin/login")
def login(body: Payload, response: Response):
    data = body.model_dump(); username = os.getenv("ADMIN_USER", "admin"); stored = os.getenv("ADMIN_PASSWORD_HASH"); plain = os.getenv("ADMIN_PASSWORD", "mipar123" if os.getenv("ENVIRONMENT", "development") == "development" else "")
    valid = data.get("username") == username and ((stored and password_hash.verify(data.get("password", ""), stored)) or (plain and secrets.compare_digest(data.get("password", ""), plain)))
    if not valid: raise HTTPException(401, "Usuario o contraseña incorrectos")
    token = jwt.encode({"sub": username, "exp": datetime.now(timezone.utc) + timedelta(hours=8)}, os.getenv("JWT_SECRET", "change-me-in-production"), algorithm="HS256")
    response.set_cookie("mipar_admin", token, httponly=True, secure=os.getenv("ENVIRONMENT") == "production", samesite="lax", max_age=28800); return {"authenticated": True, "username": username}


@app.post("/api/admin/logout")
def logout(response: Response): response.delete_cookie("mipar_admin"); return {"authenticated": False}


@app.get("/api/admin/session")
def session(user: str = Depends(admin_session)): return {"authenticated": True, "username": user}


@app.get("/api/admin/orders")
def admin_orders(_: str = Depends(admin_session), db: Session = Depends(db_session)): return [order_json(o) for o in db.scalars(select(Order).order_by(Order.created_at.desc())).all()]


@app.patch("/api/admin/orders/{order_id}")
def update_order(order_id: str, body: Payload, _: str = Depends(admin_session), db: Session = Depends(db_session)):
    order = db.get(Order, order_id); status = body.model_dump().get("status")
    if not order: raise HTTPException(404, "Pedido no encontrado")
    if status not in ["nuevo", "confirmado", "vendido"]: raise HTTPException(400, "Estado inválido")
    if order.status == "vendido" and status != "vendido": raise HTTPException(409, "La venta concretada no puede cambiar")
    order.status = status
    if status == "vendido" and order.sold_at is None:
        order.sold_at = datetime.now(timezone.utc)
    db.commit(); return order_json(order)


@app.get("/api/admin/product-requests")
def admin_requests(_: str = Depends(admin_session), db: Session = Depends(db_session)): return [request_json(r) for r in db.scalars(select(ProductRequest).order_by(ProductRequest.created_at.desc())).all()]


@app.patch("/api/admin/product-requests/{request_id}")
def update_request(request_id: str, body: Payload, _: str = Depends(admin_session), db: Session = Depends(db_session)):
    req = db.get(ProductRequest, request_id); status = body.model_dump().get("status")
    if not req: raise HTTPException(404, "Solicitud no encontrada")
    if status not in ["nueva", "contactada", "cerrada"]: raise HTTPException(400, "Estado inválido")
    req.status = status; db.commit(); return request_json(req)


@app.get("/api/admin/zones")
def admin_zones(_: str = Depends(admin_session), db: Session = Depends(db_session)): return [zone_json(z) for z in db.scalars(select(Zone)).all()]


@app.put("/api/admin/zones/{zone_id}")
def save_zone(zone_id: str, body: Payload, _: str = Depends(admin_session), db: Session = Depends(db_session)):
    data = body.model_dump(); zone = db.get(Zone, zone_id) or Zone(id=zone_id); zone.name=data["name"].strip(); zone.fee=data["fee"]; zone.weekdays=data["weekdays"]; zone.delivery_times=data["deliveryTimes"]; zone.available=bool(data["available"]); db.add(zone); db.commit(); return zone_json(zone)


@app.delete("/api/admin/zones/{zone_id}", status_code=204)
def delete_zone(zone_id: str, _: str = Depends(admin_session), db: Session = Depends(db_session)):
    zone = db.get(Zone, zone_id)
    if not zone: raise HTTPException(404, "Zona no encontrada")
    if db.scalar(select(Order.id).where(Order.zone_id == zone_id).limit(1)): raise HTTPException(409, "La zona tiene pedidos registrados")
    db.delete(zone); db.commit()


@app.put("/api/admin/products/{product_id}")
def save_product(product_id: str, body: Payload, _: str = Depends(admin_session), db: Session = Depends(db_session)):
    data=body.model_dump(); variants=data.get("variants") or []
    keys=set()
    for variant in variants:
        size=variant.get("size"); stock=variant.get("stock"); color=str(variant.get("color", "")).strip(); key=(color, size)
        if not color or isinstance(size, bool) or not isinstance(size, (int, float)) or size < 1 or not float(size * 2).is_integer() or isinstance(stock, bool) or not isinstance(stock, int) or stock < 0 or key in keys:
            raise HTTPException(400, "Las tallas deben avanzar de medio número (por ejemplo, 26 o 26.5) y las existencias deben ser enteras")
        keys.add(key)
    product=db.get(Product, product_id) or Product(id=product_id); product.name=data["name"].strip(); product.category=data["category"].strip(); product.price=data["price"]; sale_price=data.get("salePrice"); product.sale_price=float(sale_price) if sale_price and 0 < float(sale_price) < product.price else None; product.description=data.get("description","").strip(); product.variants=data["variants"]; product.colors=list(dict.fromkeys(v["color"] for v in data["variants"])); images=list(dict.fromkeys(image.strip() for image in (data.get("images") or [data.get("image", "")]) if image and image.strip())); product.images=images; product.image=images[0] if images else ""; product.tone=data.get("tone","sand"); product.badge=data.get("badge") or None; product.request_only=bool(data.get("requestOnly")); db.add(product); db.commit(); return product_json(product)


@app.delete("/api/admin/products/{product_id}", status_code=204)
def delete_product(product_id: str, _: str = Depends(admin_session), db: Session = Depends(db_session)):
    product=db.get(Product, product_id)
    if not product: raise HTTPException(404, "Producto no encontrado")
    if any(product_id == item.get("productId") for o in db.scalars(select(Order)).all() for item in o.items) or db.scalar(select(ProductRequest.id).where(ProductRequest.product_id == product_id).limit(1)): raise HTTPException(409, "El producto tiene pedidos o solicitudes")
    db.delete(product); db.commit()


@app.get("/api/admin/inventory")
def inventory(_: str = Depends(admin_session), db: Session = Depends(db_session)): return [{"productId":p.id,"productName":p.name,**v} for p in db.scalars(select(Product)).all() for v in p.variants]


@app.get("/api/admin/dashboard")
def dashboard(_: str = Depends(admin_session), db: Session = Depends(db_session)):
    orders=db.scalars(select(Order)).all(); sold=[o for o in orders if o.status=="vendido"]; by_day={}
    for o in sold:
        day=(o.sold_at or o.created_at).date().isoformat(); entry=by_day.setdefault(day,{"date":day,"orders":0,"total":0}); entry["orders"]+=1; entry["total"]+=o.total
    products=db.scalars(select(Product)).all(); return {"soldCount":len(sold),"soldTotal":sum(o.total for o in sold),"pendingCount":sum(o.status!="vendido" for o in orders),"availablePairs":sum(v["stock"] for p in products for v in p.variants),"salesByDay":sorted(by_day.values(),key=lambda x:x["date"],reverse=True)}


@app.post("/api/admin/uploads")
async def upload_image(file: UploadFile = File(...), _: str = Depends(admin_session)):
    if file.content_type not in ["image/jpeg","image/png","image/webp"]: raise HTTPException(400,"Usa una imagen JPG, PNG o WebP")
    content=await file.read(5_000_001)
    if len(content)>5_000_000: raise HTTPException(413,"La imagen debe pesar menos de 5 MB")
    if not os.getenv("BLOB_READ_WRITE_TOKEN"): raise HTTPException(503,"Falta configurar BLOB_READ_WRITE_TOKEN")
    safe_name="".join(c for c in (file.filename or "image") if c.isalnum() or c in ".-_")
    async with AsyncBlobClient() as blob:
        result=await blob.put(f"products/{safe_name}",content,access="public",content_type=file.content_type,add_random_suffix=True)
    return {"url": result.url, "pathname": result.pathname}
