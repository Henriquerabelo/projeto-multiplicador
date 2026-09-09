import bcrypt
import jwt
from datetime import datetime, timedelta
from typing import Optional, List
import uuid

from fastapi import Request, HTTPException, Depends, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

import database
import models
import os

SECRET_KEY = os.getenv("SECRET_KEY", "bradesco-multiplicador-jwt-secret-key-2026-auth")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None

def get_current_user_optional(request: Request, db: Optional[Session] = None) -> Optional[models.Usuario]:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            
    if not token:
        return None
        
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        return None
        
    user_id = payload.get("sub")
    try:
        user_uuid = uuid.UUID(user_id)
    except Exception:
        return None
        
    close_db = False
    if db is None:
        db = database.SessionLocal()
        close_db = True

    try:
        user = db.query(models.Usuario).filter(models.Usuario.id == user_uuid, models.Usuario.ativo == True).first()
        return user
    finally:
        if close_db:
            db.close()

def require_login(request: Request, db: Session = Depends(database.get_db)) -> models.Usuario:
    user = get_current_user_optional(request, db)
    if not user:
        accept = request.headers.get("accept", "")
        if "text/html" in accept or True:
            raise HTTPException(status_code=303, detail="Redirecionando para login", headers={"Location": "/login"})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Não autenticado. Por favor, realize o login."
        )
    return user

def require_admin(request: Request, user: models.Usuario = Depends(require_login)) -> models.Usuario:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito para administradores."
        )
    return user

def get_allowed_vendedor_ids(user: models.Usuario, db: Session) -> Optional[List[uuid.UUID]]:
    """
    Returns:
      - None: if admin (unrestricted access to all sellers)
      - List[UUID]: if coordenador (their own ID + IDs of their supervised sellers)
      - List[UUID]: if vendedor (only their own ID)
    """
    if not user or user.role == "admin":
        return None
        
    if user.role == "coordenador":
        vids = []
        if user.vendedor_id:
            vids.append(user.vendedor_id)
            subordinates = db.query(models.Vendedor.id).filter(
                models.Vendedor.coordenador_id == user.vendedor_id
            ).all()
            for s in subordinates:
                if s[0] not in vids:
                    vids.append(s[0])
        return vids

    if user.role == "vendedor":
        return [user.vendedor_id] if user.vendedor_id else []

    return []
