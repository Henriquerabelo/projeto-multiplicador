import bcrypt
import jwt
import re
from datetime import datetime, timedelta, timezone
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
DEFAULT_INITIAL_PASSWORD = "Multiplicador@2026"

def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    Valida política de senha forte:
    - Mínimo de 8 caracteres
    - Pelo menos 1 letra
    - Pelo menos 1 número
    - Pelo menos 1 caractere especial
    """
    if not password or len(password) < 8:
        return False, "A senha deve ter no mínimo 8 caracteres."
    if not re.search(r'[a-zA-Z]', password):
        return False, "A senha deve conter pelo menos uma letra."
    if not re.search(r'[0-9]', password):
        return False, "A senha deve conter pelo menos um número."
    if not re.search(r'[^a-zA-Z0-9]', password):
        return False, "A senha deve conter pelo menos um caractere especial (ex: @, #, $, %, &, *)."
    return True, ""

def user_requires_password_change(user: models.Usuario) -> tuple[bool, str]:
    """
    Verifica se o usuário precisa alterar a senha:
    - Primeiro acesso: True
    - Senha com 90 dias ou mais: True
    Retorna (precisa_trocar: bool, motivo: str)
    """
    if not user:
        return False, ""
        
    if getattr(user, 'primeiro_acesso', False):
        return True, "primeiro_acesso"
        
    dt_alteracao = getattr(user, 'senha_alterada_em', None)
    if dt_alteracao:
        now = datetime.now(timezone.utc)
        if dt_alteracao.tzinfo is None:
            dt_alteracao = dt_alteracao.replace(tzinfo=timezone.utc)
        dias = (now - dt_alteracao).days
        if dias >= 90:
            return True, "expirada"
            
    return False, ""


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
        
    # Intercepta se o usuário precisa trocar de senha (primeiro acesso ou expirada)
    precisa_trocar, _ = user_requires_password_change(user)
    if precisa_trocar:
        path = request.url.path
        if not path.startswith("/trocar-senha") and not path.startswith("/logout"):
            raise HTTPException(status_code=303, detail="Redirecionando para troca de senha obrigatória", headers={"Location": "/trocar-senha"})

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
