from fastapi import FastAPI, Depends, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, date
import pandas as pd
import shutil
import os
import re
import uuid
import csv
import io

import models
import database
import crud
import auth

# Garante a criação de todas as tabelas no banco de dados na inicialização
models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="Multiplicador - Bradesco Expresso")

# Templates setup
templates = Jinja2Templates(directory="templates")

# Exception handler to redirect to login on 303 or unauthorized
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 303 and "Location" in exc.headers:
        return RedirectResponse(url=exc.headers["Location"], status_code=303)
    if exc.status_code == 401:
        return RedirectResponse(url="/login", status_code=303)
    if exc.status_code == 403:
        user = auth.get_current_user_optional(request)
        return templates.TemplateResponse(request, "login.html", {
            "request": request,
            "error": exc.detail or "Acesso não autorizado para o seu perfil."
        }, status_code=403)
    return HTMLResponse(content=f"<div style='font-family: sans-serif; padding: 40px; text-align: center;'><h2>Erro {exc.status_code}</h2><p>{exc.detail}</p><a href='/' style='color: #ef4444; font-weight: bold;'>Voltar ao Início</a></div>", status_code=exc.status_code)

# Helper functions for processing uploads
def extrair_data_arquivo(nome_arquivo: str) -> date:
    match = re.search(r'(\d{4}-\d{2}-\d{2})', nome_arquivo)
    if not match:
        raise ValueError("Nome de arquivo não contém data no padrão aaaa-mm-dd.")
    return datetime.strptime(match.group(1), "%Y-%m-%d").date()

def normalizar_elegivel(bloqueado: str, referencia: str) -> str:
    if str(bloqueado).strip().lower() == 'bloqueado':
        return 'Não'
    return 'Sim' if str(referencia).strip().lower() == 'sim' else 'Não'

def clean_key(k):
    if isinstance(k, str):
        return k.strip()
    try:
        return str(int(k)).strip()
    except Exception:
        return str(k).strip()

def to_date(valor):
    if pd.isna(valor) or str(valor).strip() in ('', 'nan', 'NaT'):
        return None
    try:
        from dateutil import parser as dateparser
        return dateparser.parse(str(valor).strip(), dayfirst=True).date()
    except Exception:
        return None

def to_int(valor) -> int:
    try:
        v = str(valor).strip()
        return int(float(v)) if v not in ('', 'nan') else 0
    except Exception:
        return 0

def to_decimal(valor) -> float:
    try:
        v = str(valor).strip()
        return float(v) if v not in ('', 'nan') else 0.0
    except Exception:
        return 0.0

# ─────────────────────────────────────────────
# AUTHENTICATION ROUTES
# ─────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(database.get_db), error: str = None):
    user = auth.get_current_user_optional(request, db)
    if user:
        if user.role == "vendedor" and user.vendedor_id:
            return RedirectResponse(f"/vendedor/{user.vendedor_id}", status_code=303)
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": error})

@app.post("/login")
async def login_submit(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(database.get_db)
):
    user = crud.get_user_by_login(db, login)
    if not user or not auth.verify_password(password, user.senha_hash):
        return templates.TemplateResponse(request, "login.html", {
            "request": request,
            "error": "Usuário ou senha incorretos."
        }, status_code=400)
        
    token = auth.create_access_token({
        "sub": str(user.id),
        "role": user.role,
        "nome": user.nome
    })
    
    precisa_trocar, _ = auth.user_requires_password_change(user)
    if precisa_trocar:
        target_url = "/trocar-senha"
    else:
        target_url = "/"
        if user.role == "vendedor" and user.vendedor_id:
            target_url = f"/vendedor/{user.vendedor_id}"
        
    response = RedirectResponse(url=target_url, status_code=303)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=86400 * 7,
        samesite="lax"
    )
    return response

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="access_token")
    return response

@app.get("/trocar-senha", response_class=HTMLResponse)
async def trocar_senha_page(
    request: Request,
    db: Session = Depends(database.get_db),
    error: str = None,
    success_msg: str = None
):
    user = auth.get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
        
    precisa_trocar, motivo = auth.user_requires_password_change(user)
    return templates.TemplateResponse(request, "trocar_senha.html", {
        "request": request,
        "current_user": user,
        "motivo": motivo,
        "is_forced": precisa_trocar,
        "error": error,
        "success_msg": success_msg
    })

@app.post("/trocar-senha")
async def trocar_senha_submit(
    request: Request,
    senha_atual: str = Form(...),
    nova_senha: str = Form(...),
    confirmar_senha: str = Form(...),
    db: Session = Depends(database.get_db)
):
    user = auth.get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
        
    precisa_trocar, motivo = auth.user_requires_password_change(user)
    
    # 1. Verifica senha atual
    if not auth.verify_password(senha_atual, user.senha_hash):
        return templates.TemplateResponse(request, "trocar_senha.html", {
            "request": request,
            "current_user": user,
            "motivo": motivo,
            "is_forced": precisa_trocar,
            "error": "A senha atual informada está incorreta."
        }, status_code=400)
        
    # 2. Verifica igualdade com a confirmação
    if nova_senha != confirmar_senha:
        return templates.TemplateResponse(request, "trocar_senha.html", {
            "request": request,
            "current_user": user,
            "motivo": motivo,
            "is_forced": precisa_trocar,
            "error": "A confirmação da nova senha não coincide com a nova senha."
        }, status_code=400)
        
    # 3. Impede reutilização da mesma senha
    if auth.verify_password(nova_senha, user.senha_hash):
        return templates.TemplateResponse(request, "trocar_senha.html", {
            "request": request,
            "current_user": user,
            "motivo": motivo,
            "is_forced": precisa_trocar,
            "error": "A nova senha não pode ser idêntica à senha atual."
        }, status_code=400)
        
    # 4. Valida política de senha forte
    valida, erro_politica = auth.validate_password_strength(nova_senha)
    if not valida:
        return templates.TemplateResponse(request, "trocar_senha.html", {
            "request": request,
            "current_user": user,
            "motivo": motivo,
            "is_forced": precisa_trocar,
            "error": erro_politica
        }, status_code=400)
        
    # 5. Atualiza dados no banco
    from datetime import datetime, timezone
    user.senha_hash = auth.hash_password(nova_senha)
    user.senha_alterada_em = datetime.now(timezone.utc)
    user.primeiro_acesso = False
    db.commit()
    
    target_url = "/"
    if user.role == "vendedor" and user.vendedor_id:
        target_url = f"/vendedor/{user.vendedor_id}"
        
    return RedirectResponse(url=target_url, status_code=303)

# ─────────────────────────────────────────────
# APPLICATION ROUTES
# ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request, 
    ref: str = None, 
    user: models.Usuario = Depends(auth.require_login),
    db: Session = Depends(database.get_db)
):
    if user.role == "vendedor" and user.vendedor_id:
        return RedirectResponse(f"/vendedor/{user.vendedor_id}" + (f"?ref={ref}" if ref else ""), status_code=303)

    months = crud.get_available_months(db)
    
    if not months:
        return templates.TemplateResponse(request, "dashboard.html", {
            "request": request, "active_page": "dashboard", "current_user": user,
            "months": [], "selected_month": None, "ranking": [],
            "total_lojas": 0, "total_elegiveis1": 0, "total_elegiveis2": 0,
            "total_pontos1": 0, "total_pontos2": 0, "resumo": {}, "volumes": {}
        })

    # Selected month
    selected_month = None
    if ref:
        try:
            selected_month = datetime.strptime(ref, "%Y-%m-%d").date()
        except Exception:
            pass
            
    if not selected_month or selected_month not in months:
        selected_month = months[0]

    allowed_vendedor_ids = auth.get_allowed_vendedor_ids(user, db)
    ranking = crud.get_ranking(db, selected_month, allowed_vendedor_ids=allowed_vendedor_ids)

    # KPIs
    total_lojas = sum(r['total_lojas'] for r in ranking)
    total_elegiveis1 = sum(r['lojas_elegiveis1'] for r in ranking)
    total_elegiveis2 = sum(r['lojas_elegiveis2'] for r in ranking)
    total_pontos1 = sum(r['pontuacao1_total'] for r in ranking)
    total_pontos2 = sum(r['pontuacao2_total'] for r in ranking)

    # Summary table counts (Sim, Próximo, Não, Zero)
    sql_resumo = """
        SELECT 
            count(*) FILTER (WHERE elegivel1 = 'Sim') as sim1,
            count(*) FILTER (WHERE elegivel1 = 'Próximo') as prox1,
            count(*) FILTER (WHERE elegivel1 = 'Não') as nao1,
            count(*) FILTER (WHERE elegivel1 = 'Zero') as zero1,
            count(*) FILTER (WHERE elegivel2 = 'Sim') as sim2,
            count(*) FILTER (WHERE elegivel2 = 'Próximo') as prox2,
            count(*) FILTER (WHERE elegivel2 = 'Não') as nao2,
            count(*) FILTER (WHERE elegivel2 = 'Zero') as zero2,
            count(*) as total
        FROM vw_pontuacao
        WHERE referencia = :ref AND is_coringa = false
    """
    resumo_params = {"ref": selected_month}
    if allowed_vendedor_ids is not None:
        if not allowed_vendedor_ids:
            sql_resumo += " AND 1=0"
        else:
            sql_resumo += " AND vendedor_id::text = ANY(:vids)"
            resumo_params["vids"] = [str(vid) for vid in allowed_vendedor_ids]

    resumo_row = db.execute(text(sql_resumo), resumo_params).fetchone()
    resumo = dict(resumo_row._mapping) if resumo_row else {}
    if resumo:
        resumo['ativas1'] = (resumo.get('sim1') or 0) + (resumo.get('prox1') or 0) + (resumo.get('nao1') or 0)
        resumo['ativas2'] = (resumo.get('sim2') or 0) + (resumo.get('prox2') or 0) + (resumo.get('nao2') or 0)

    # Fetch Business Volume Stats
    sql_volumes = """
        SELECT 
            COALESCE(SUM(p.qtd_contas), 0) as contas_pf,
            COALESCE(SUM(p.qtd_contas_pj), 0) as contas_pj,
            COALESCE(SUM(p.qtd_contas_folha), 0) as contas_folha,
            COALESCE(SUM(p.vlr_lime), 0) as vlr_lime,
            COALESCE(SUM(p.vlr_consignado), 0) as vlr_consignado,
            COALESCE(SUM(p.vlr_credito_parcel), 0) as vlr_credito_parcel,
            COALESCE(SUM(p.vlr_ches), 0) as vlr_ches,
            COALESCE(SUM(p.qtd_consorcio), 0) as consorcio,
            COALESCE(SUM(p.qtd_microsseguro), 0) as seguro_micro,
            COALESCE(SUM(p.qtd_super_protegido), 0) as seguro_super,
            COALESCE(SUM(p.qtd_micro_vivavida), 0) as seguro_vivavida,
            COALESCE(SUM(p.qtd_plano_odonto), 0) as seguro_odonto,
            COALESCE(SUM(p.qtd_seg_residencial), 0) as seguro_residencial,
            COALESCE(SUM(p.qtd_seg_cartao_deb), 0) as seguro_cartao,
            COALESCE(SUM(p.qtd_trx_contabil), 0) as trx_contabil,
            COALESCE(SUM(p.qtd_cesta_serv), 0) as cesta_serv,
            COALESCE(SUM(p.qtd_mobilidade), 0) as mobilidade,
            COALESCE(SUM(p.qtd_mtoken), 0) as mtoken,
            COALESCE(SUM(p.qtd_cartao_emitido), 0) as cartao_emitido
        FROM producao_lojas p
        JOIN lojas l ON l.id = p.loja_id
        JOIN vendedores v ON v.id = p.vendedor_id
        WHERE p.referencia = :ref AND v.is_coringa = false AND l.status_treinamento = 'TREINADO'
    """
    vol_params = {"ref": selected_month}
    if allowed_vendedor_ids is not None:
        if not allowed_vendedor_ids:
            sql_volumes += " AND 1=0"
        else:
            sql_volumes += " AND p.vendedor_id::text = ANY(:vids)"
            vol_params["vids"] = [str(vid) for vid in allowed_vendedor_ids]

    volumes_row = db.execute(text(sql_volumes), vol_params).fetchone()
    volumes = dict(volumes_row._mapping) if volumes_row else {}

    ultima_importacao = crud.get_ultima_importacao(db)

    return templates.TemplateResponse(request, "dashboard.html", {
        "request": request,
        "active_page": "dashboard",
        "current_user": user,
        "months": months,
        "selected_month": selected_month,
        "ranking": ranking,
        "total_lojas": total_lojas,
        "total_elegiveis1": total_elegiveis1,
        "total_elegiveis2": total_elegiveis2,
        "total_pontos1": total_pontos1,
        "total_pontos2": total_pontos2,
        "resumo": resumo,
        "volumes": volumes,
        "ultima_importacao": ultima_importacao
    })

@app.get("/dashboard/exportar-enquadramento")
async def exportar_enquadramento_csv(
    request: Request,
    ref: str = None,
    user: models.Usuario = Depends(auth.require_login),
    db: Session = Depends(database.get_db)
):
    months = crud.get_available_months(db)
    if not months:
        raise HTTPException(status_code=404, detail="Nenhum dado disponível para exportação.")

    selected_month = None
    if ref:
        try:
            selected_month = datetime.strptime(ref, "%Y-%m-%d").date()
        except Exception:
            pass
    if not selected_month or selected_month not in months:
        selected_month = months[0]

    allowed_vendedor_ids = auth.get_allowed_vendedor_ids(user, db)
    linhas = crud.get_enquadramento_detalhado(db, selected_month, allowed_vendedor_ids=allowed_vendedor_ids)

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_MINIMAL)

    # Header
    writer.writerow([
        "Chave Loja",
        "Nome da Loja",
        "Município",
        "UF",
        "Agência",
        "Agência/PA",
        "Regional",
        "Assessor Responsável",
        "Treinamento",
        "Bloqueio",
        "Enquadramento Sem Cesta",
        "Pontos Sem Cesta",
        "Enquadramento Com Cesta",
        "Pontos Com Cesta",
        "Trx Contábeis",
        "Contas Abertas",
        "Cesta de Serviços",
        "Crédito Total (R$)",
        "Seguros Total (Qtd)",
        "Consórcios (Qtd)",
        "Cartões Emitidos"
    ])

    for l in linhas:
        vlr_credito_fmt = f"{float(l['vlr_credito_total']):.2f}".replace('.', ',') if l.get('vlr_credito_total') else "0,00"
        writer.writerow([
            l.get('chave_loja', '') or '',
            l.get('nome_loja', '') or '',
            l.get('municipio', '') or '',
            l.get('uf', '') or '',
            l.get('agencia', '') or '',
            l.get('ag_pacb', '') or '',
            l.get('regional', '') or '',
            l.get('vendedor', '') or 'Sem Assessor',
            l.get('status_treinamento', '') or 'N/D',
            l.get('status_bloqueio', '') or 'Desbloqueado',
            l.get('elegivel1', '') or 'Não',
            l.get('pontuacao1', 0),
            l.get('elegivel2', '') or 'Não',
            l.get('pontuacao2', 0),
            l.get('qtd_trx_contabil', 0),
            l.get('qtd_contas', 0),
            l.get('qtd_cesta_serv', 0),
            vlr_credito_fmt,
            l.get('qtd_seguros_total', 0),
            l.get('qtd_consorcio', 0),
            l.get('qtd_cartao_emitido', 0)
        ])

    csv_data = output.getvalue().encode('utf-8-sig')
    filename = f"enquadramento_detalhado_{selected_month.strftime('%Y_%m')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "text/csv; charset=utf-8-sig"
        }
    )

@app.get("/vendedor/{vendedor_id}", response_class=HTMLResponse)
async def vendedor_detail(
    request: Request, 
    vendedor_id: str, 
    ref: str = None, 
    user: models.Usuario = Depends(auth.require_login),
    db: Session = Depends(database.get_db)
):
    # Check access permission
    allowed_vendedor_ids = auth.get_allowed_vendedor_ids(user, db)
    if allowed_vendedor_ids is not None:
        try:
            target_vid = uuid.UUID(vendedor_id)
            if target_vid not in allowed_vendedor_ids:
                raise HTTPException(status_code=403, detail="Você não possui permissão para visualizar os dados deste assessor.")
        except ValueError:
            raise HTTPException(status_code=400, detail="ID de assessor inválido.")

    months = crud.get_available_months(db)
    if not months:
        return RedirectResponse("/")
        
    selected_month = None
    if ref:
        try:
            selected_month = datetime.strptime(ref, "%Y-%m-%d").date()
        except Exception:
            pass
    if not selected_month or selected_month not in months:
        selected_month = months[0]

    # Find seller details
    seller_query = db.execute(
        models.Vendedor.__table__.select().where(models.Vendedor.id == uuid.UUID(vendedor_id))
    ).fetchone()
    
    if not seller_query:
        raise HTTPException(status_code=404, detail="Assessor não encontrado")

    vendedor_nome = seller_query.nome
    
    lojas = crud.get_vendedor_details(db, selected_month, vendedor_id)
    
    # Get all regionals from the assessor's stores
    store_regionals = sorted(list(set(l['regional'] for l in lojas if l.get('regional'))))
    regional_nome = ", ".join(store_regionals) if store_regionals else "GERAL"
    
    total_elegiveis1 = sum(1 for l in lojas if l['elegivel1'] == 'Sim')
    total_elegiveis2 = sum(1 for l in lojas if l['elegivel2'] == 'Sim')
    total_pontos1 = sum(l['pontuacao1'] for l in lojas)
    total_pontos2 = sum(l['pontuacao2'] for l in lojas)

    # Group by Regional and then by Agencia
    regional_groups = {}
    for l in lojas:
        reg = l.get('regional') or 'GERAL'
        ag = l.get('agencia') or 'Sem Agência'
        
        if reg not in regional_groups:
            regional_groups[reg] = {
                'regional': reg,
                'agencias': {}
            }
            
        if ag not in regional_groups[reg]['agencias']:
            regional_groups[reg]['agencias'][ag] = {
                'agencia': ag,
                'lojas': [],
                'total_lojas': 0,
                'total_elegiveis1': 0,
                'total_elegiveis2': 0,
                'total_pontos1': 0,
                'total_pontos2': 0
            }
            
        ag_dict = regional_groups[reg]['agencias'][ag]
        ag_dict['lojas'].append(l)
        ag_dict['total_lojas'] += 1
        if l['elegivel1'] == 'Sim':
            ag_dict['total_elegiveis1'] += 1
        if l['elegivel2'] == 'Sim':
            ag_dict['total_elegiveis2'] += 1
        ag_dict['total_pontos1'] += l['pontuacao1']
        ag_dict['total_pontos2'] += l['pontuacao2']

    # Convert to sorted list structure
    regional_groups_list = []
    for reg, reg_data in sorted(regional_groups.items()):
        sorted_ags = sorted(reg_data['agencias'].values(), key=lambda x: x['agencia'])
        regional_groups_list.append({
            'regional': reg,
            'agencias': sorted_ags
        })

    # Summary table counts (Sim, Próximo, Não, Zero) for this specific seller
    sql_resumo = """
        SELECT 
            count(*) FILTER (WHERE elegivel1 = 'Sim') as sim1,
            count(*) FILTER (WHERE elegivel1 = 'Próximo') as prox1,
            count(*) FILTER (WHERE elegivel1 = 'Não') as nao1,
            count(*) FILTER (WHERE elegivel1 = 'Zero') as zero1,
            count(*) FILTER (WHERE elegivel2 = 'Sim') as sim2,
            count(*) FILTER (WHERE elegivel2 = 'Próximo') as prox2,
            count(*) FILTER (WHERE elegivel2 = 'Não') as nao2,
            count(*) FILTER (WHERE elegivel2 = 'Zero') as zero2,
            count(*) as total
        FROM vw_pontuacao
        WHERE referencia = :ref AND vendedor_id = :vendedor_id
    """
    resumo_row = db.execute(text(sql_resumo), {"ref": selected_month, "vendedor_id": uuid.UUID(vendedor_id)}).fetchone()
    resumo = dict(resumo_row._mapping) if resumo_row else {}
    if resumo:
        resumo['ativas1'] = (resumo.get('sim1') or 0) + (resumo.get('prox1') or 0) + (resumo.get('nao1') or 0)
        resumo['ativas2'] = (resumo.get('sim2') or 0) + (resumo.get('prox2') or 0) + (resumo.get('nao2') or 0)

    # Fetch Business Volume Stats for this specific seller
    sql_volumes = """
        SELECT 
            COALESCE(SUM(p.qtd_contas), 0) as contas_pf,
            COALESCE(SUM(p.qtd_contas_pj), 0) as contas_pj,
            COALESCE(SUM(p.qtd_contas_folha), 0) as contas_folha,
            COALESCE(SUM(p.vlr_lime), 0) as vlr_lime,
            COALESCE(SUM(p.vlr_consignado), 0) as vlr_consignado,
            COALESCE(SUM(p.vlr_credito_parcel), 0) as vlr_credito_parcel,
            COALESCE(SUM(p.vlr_ches), 0) as vlr_ches,
            COALESCE(SUM(p.qtd_consorcio), 0) as consorcio,
            COALESCE(SUM(p.qtd_microsseguro), 0) as seguro_micro,
            COALESCE(SUM(p.qtd_super_protegido), 0) as seguro_super,
            COALESCE(SUM(p.qtd_micro_vivavida), 0) as seguro_vivavida,
            COALESCE(SUM(p.qtd_plano_odonto), 0) as seguro_odonto,
            COALESCE(SUM(p.qtd_seg_residencial), 0) as seguro_residencial,
            COALESCE(SUM(p.qtd_seg_cartao_deb), 0) as seguro_cartao,
            COALESCE(SUM(p.qtd_trx_contabil), 0) as trx_contabil,
            COALESCE(SUM(p.qtd_cesta_serv), 0) as cesta_serv,
            COALESCE(SUM(p.qtd_mobilidade), 0) as mobilidade,
            COALESCE(SUM(p.qtd_mtoken), 0) as mtoken,
            COALESCE(SUM(p.qtd_cartao_emitido), 0) as cartao_emitido
        FROM producao_lojas p
        JOIN lojas l ON l.id = p.loja_id
        WHERE p.referencia = :ref AND p.vendedor_id = :vendedor_id AND l.status_treinamento = 'TREINADO'
    """
    volumes_row = db.execute(text(sql_volumes), {"ref": selected_month, "vendedor_id": uuid.UUID(vendedor_id)}).fetchone()
    volumes = dict(volumes_row._mapping) if volumes_row else {}

    return templates.TemplateResponse(request, "vendedor.html", {
        "request": request,
        "active_page": "dashboard",
        "current_user": user,
        "months": months,
        "selected_month": selected_month,
        "vendedor_id": vendedor_id,
        "vendedor_nome": vendedor_nome,
        "regional_nome": regional_nome,
        "lojas": lojas,
        "total_elegiveis1": total_elegiveis1,
        "total_elegiveis2": total_elegiveis2,
        "total_pontos1": total_pontos1,
        "total_pontos2": total_pontos2,
        "regional_groups": regional_groups_list,
        "resumo": resumo,
        "volumes": volumes
    })

@app.get("/lojas", response_class=HTMLResponse)
async def lojas_list(
    request: Request, 
    search: str = None, 
    vendedor_id: str = None, 
    user: models.Usuario = Depends(auth.require_login),
    db: Session = Depends(database.get_db)
):
    allowed_vendedor_ids = auth.get_allowed_vendedor_ids(user, db)
    lojas = crud.get_stores_list(db, search, vendedor_id, allowed_vendedor_ids=allowed_vendedor_ids)
    vendedores = crud.get_vendedores(db, allowed_vendedor_ids=allowed_vendedor_ids)
    
    return templates.TemplateResponse(request, "lojas.html", {
        "request": request,
        "active_page": "lojas",
        "current_user": user,
        "lojas": lojas,
        "vendedores": vendedores,
        "search": search or "",
        "selected_vendedor": vendedor_id or ""
    })

@app.get("/lojas/exportar")
async def exportar_lojas_csv(
    request: Request,
    search: str = None,
    vendedor_id: str = None,
    user: models.Usuario = Depends(auth.require_login),
    db: Session = Depends(database.get_db)
):
    allowed_vendedor_ids = auth.get_allowed_vendedor_ids(user, db)
    lojas = crud.get_stores_list(db, search, vendedor_id, allowed_vendedor_ids=allowed_vendedor_ids)

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_MINIMAL)
    
    # CSV Header
    writer.writerow([
        "Chave Loja",
        "Nome da Loja",
        "Município",
        "UF",
        "Agência/PA",
        "Regional",
        "Assessor Responsável",
        "Última Importação"
    ])

    for l in lojas:
        data_ult = l['ultima_data'].strftime('%d/%m/%Y') if l.get('ultima_data') else 'Nenhuma'
        writer.writerow([
            l.get('chave_loja', '') or '',
            l.get('nome_loja', '') or '',
            l.get('municipio', '') or '',
            l.get('uf', '') or '',
            l.get('ag_pacb', '') or '',
            l.get('regional', '') or '',
            l.get('vendedor', '') or 'Sem Assessor',
            data_ult
        ])

    csv_data = output.getvalue().encode('utf-8-sig')
    filename = f"carteira_lojas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "text/csv; charset=utf-8-sig"
        }
    )

@app.post("/api/lojas/{store_id}/carteira")
async def update_carteira(
    store_id: str, 
    vendedor_id: str = Form(None), 
    user: models.Usuario = Depends(auth.require_admin),
    db: Session = Depends(database.get_db)
):
    crud.update_store_vendedor(db, store_id, vendedor_id)
    return {"status": "success"}

@app.get("/vendedores", response_class=HTMLResponse)
async def vendedores_list(
    request: Request, 
    user: models.Usuario = Depends(auth.require_admin),
    db: Session = Depends(database.get_db)
):
    vendedores = crud.get_vendedores(db)
    regionais = crud.get_regionais(db)
    return templates.TemplateResponse(request, "vendedores.html", {
        "request": request,
        "active_page": "vendedores",
        "current_user": user,
        "vendedores": vendedores,
        "regionais": regionais
    })

@app.post("/vendedores")
async def create_vendedor(
    request: Request, 
    nome: str = Form(...), 
    regional_id: str = Form(...), 
    coordenador_id: str = Form(None),
    user: models.Usuario = Depends(auth.require_admin),
    db: Session = Depends(database.get_db)
):
    crud.create_vendedor(db, nome, regional_id, coordenador_id)
    return RedirectResponse("/vendedores", status_code=303)

@app.get("/importar", response_class=HTMLResponse)
async def importar_page(
    request: Request, 
    user: models.Usuario = Depends(auth.require_admin),
    db: Session = Depends(database.get_db)
):
    importacoes = crud.get_importacoes(db)
    ultima_importacao = crud.get_ultima_importacao(db)
    return templates.TemplateResponse(request, "importacao.html", {
        "request": request,
        "active_page": "importar",
        "current_user": user,
        "importacoes": importacoes,
        "ultima_importacao": ultima_importacao
    })

@app.post("/importar")
async def processar_importacao(
    request: Request,
    file: UploadFile = File(...),
    user: models.Usuario = Depends(auth.require_admin),
    db: Session = Depends(database.get_db)
):
    filename = file.filename
    try:
        dt_arquivo = extrair_data_arquivo(filename)
    except Exception as e:
        return templates.TemplateResponse(request, "importacao.html", {
            "request": request,
            "active_page": "importar",
            "current_user": user,
            "importacoes": crud.get_importacoes(db),
            "ultima_importacao": crud.get_ultima_importacao(db),
            "error_msg": f"Erro no nome do arquivo: {str(e)}"
        })

    ref_mes = date(dt_arquivo.year, dt_arquivo.month, 1)

    # Save temp file
    temp_dir = "temp_uploads"
    os.makedirs(temp_dir, exist_ok=True)
    temp_file_path = os.path.join(temp_dir, filename)
    with open(temp_file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Read dataframe
    try:
        if filename.lower().endswith('.csv'):
            with open(temp_file_path, 'r', encoding='latin1') as f:
                first_line = f.readline()
                sep = ';' if ';' in first_line else ','
                header_count = len(first_line.split(sep))
            df_diario = pd.read_csv(
                temp_file_path,
                sep=sep,
                encoding='latin1',
                usecols=range(header_count),
                dtype=str
            )
        else:
            df_diario = pd.read_excel(temp_file_path)
    except Exception as e:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        return templates.TemplateResponse(request, "importacao.html", {
            "request": request,
            "active_page": "importar",
            "current_user": user,
            "importacoes": crud.get_importacoes(db),
            "ultima_importacao": crud.get_ultima_importacao(db),
            "error_msg": f"Erro ao ler arquivo: {str(e)}"
        })

    # Create import record
    new_import = models.Importacao(
        referencia=ref_mes,
        data_arquivo=dt_arquivo,
        arquivo_nome=filename,
        total_lojas=len(df_diario),
        status='processando'
    )
    db.add(new_import)
    db.commit()

    # Column mapping normalize
    col_mapping = {c.lower().replace(' ', '_'): c for c in df_diario.columns}
    
    def get_col_val(row, *possible_names):
        for name in possible_names:
            normalized = name.lower().replace(' ', '_')
            if normalized in col_mapping:
                return row[col_mapping[normalized]]
        return None

    erros_loja = []
    
    # Process rows
    for idx, row in df_diario.iterrows():
        ch = clean_key(get_col_val(row, 'chave_loja', 'Chave loja'))
        if not ch or ch.lower() in ('%', '0', '1', 'nan', 'nat', '', 'none'):
            continue

        # Get store
        store_query = db.query(models.Loja).filter(models.Loja.chave_loja == ch).first()
        
        if not store_query:
            # Nova loja identificada no arquivo diário!
            ag_pacb = str(get_col_val(row, 'ag_pacb', 'AG_PACB', 'Ag_Pacb') or '').strip() or None
            agencia = str(get_col_val(row, 'Agência', 'agencia', 'AGENCIA') or '').strip()
            if not agencia and ag_pacb:
                agencia = ag_pacb.split('/')[0].strip() if '/' in ag_pacb else ag_pacb
            if not agencia:
                agencia = '0000'

            # 1. Determinar o responsável a partir das lojas da mesma agência
            assigned_vendedor_id = None
            if agencia and agencia != '0000':
                sql_assessor = text("""
                    SELECT l.vendedor_id 
                    FROM lojas l
                    JOIN vendedores v ON v.id = l.vendedor_id
                    WHERE l.agencia = :ag AND NOT v.is_coringa AND l.ativo = TRUE
                    GROUP BY l.vendedor_id
                    ORDER BY count(*) DESC
                    LIMIT 1
                """)
                assessor_res = db.execute(sql_assessor, {"ag": agencia}).fetchone()
                if assessor_res:
                    assigned_vendedor_id = assessor_res[0]

            # Se a agência não tiver assessor mapeado ainda, atribui ao Coringa (Sem Responsável)
            if not assigned_vendedor_id:
                coringa = db.query(models.Vendedor).filter(models.Vendedor.is_coringa == True).first()
                assigned_vendedor_id = coringa.id if coringa else None

            # 2. Determinar a regional (filtrando eventuais totais/cálculos numéricos do Excel)
            reg_nome = str(get_col_val(row, 'ger_regional', 'GER_REGIONAL', 'regional', 'Regional') or 'GERAL').strip().upper()
            if not reg_nome or reg_nome.startswith('0.') or reg_nome.replace('.', '', 1).isdigit() or reg_nome in ('NAN', 'NONE', 'NAT'):
                reg_nome = 'GERAL'
            regional = db.query(models.Regional).filter(models.Regional.nome.ilike(reg_nome)).first()
            if not regional:
                regional = db.query(models.Regional).filter(models.Regional.nome == 'GERAL').first()
                if not regional:
                    regional = models.Regional(nome=reg_nome or 'GERAL')
                    db.add(regional)
                    db.commit()

            nome_loja = str(get_col_val(row, 'nome_loja', 'Nome loja', 'NOME_LOJA') or f'Loja {ch}').strip()
            uf = str(get_col_val(row, 'uf', 'UF') or 'CE').strip()[:2].upper()
            municipio = str(get_col_val(row, 'municipio', 'MUNICIPIO') or '').strip()
            dt_inst = to_date(get_col_val(row, 'dt_inst_tablet'))
            dt_clube = to_date(get_col_val(row, 'dt_clube'))
            dt_cert = to_date(get_col_val(row, 'dt_certificacao'))
            status_bl = 'Bloqueado' if str(get_col_val(row, 'BLOQUEADO') or '').strip().lower() == 'bloqueado' else 'Desbloqueado'
            dt_bl = to_date(get_col_val(row, 'DT_BLOQUEADO'))
            status_tr = str(get_col_val(row, 'STATUS_ANALISE', 'Status') or '').strip() or None

            store_query = models.Loja(
                chave_loja=ch,
                agencia=agencia,
                nome_loja=nome_loja,
                ag_pacb=ag_pacb,
                uf=uf,
                municipio=municipio,
                regional_id=regional.id if regional else None,
                dt_inst_tablet=dt_inst,
                dt_clube=dt_clube,
                dt_certificacao=dt_cert,
                status_bloqueio=status_bl,
                dt_bloqueado=dt_bl,
                status_treinamento=status_tr,
                ativo=True,
                vendedor_id=assigned_vendedor_id
            )
            db.add(store_query)
            db.commit()
            db.refresh(store_query)

        # Check production
        prod_db = db.query(models.ProducaoLoja).filter(
            models.ProducaoLoja.loja_id == store_query.id,
            models.ProducaoLoja.referencia == ref_mes
        ).first()

        elegivel = normalizar_elegivel(
            get_col_val(row, 'BLOQUEADO'),
            get_col_val(row, 'REFERENCIA')
        )

        try:
            if not prod_db:
                prod_db = models.ProducaoLoja(
                    loja_id=store_query.id,
                    importacao_id=new_import.id,
                    vendedor_id=store_query.vendedor_id,
                    referencia=ref_mes
                )
                db.add(prod_db)

            # Update metrics
            prod_db.importacao_id = new_import.id
            prod_db.vendedor_id = store_query.vendedor_id
            prod_db.elegivel = elegivel
            prod_db.data_ult_transacao = to_date(get_col_val(row, 'data_ult_transacao'))
            prod_db.qtd_trx_contabil = to_int(get_col_val(row, 'qtd_TrxContabil', 'qtd_trxcontabil'))
            prod_db.qtd_contas = to_int(get_col_val(row, 'qtd_contas'))
            prod_db.qtd_contas_com_deposito = to_int(get_col_val(row, 'qtd_contas_com_deposito'))
            prod_db.qtd_cesta_serv = to_int(get_col_val(row, 'qtd_cesta_serv'))
            prod_db.qtd_contas_pj = to_int(get_col_val(row, 'QTD_CONTAS_PJ', 'qtd_contas_pj'))
            prod_db.qtd_contas_folha = to_int(get_col_val(row, 'qtd_contas_folha'))
            prod_db.qtd_mobilidade = to_int(get_col_val(row, 'qtd_mobilidade'))
            prod_db.qtd_mtoken = to_int(get_col_val(row, 'qtd_mtoken'))
            prod_db.qtd_cartao_emitido = to_int(get_col_val(row, 'QTD_CARTAO_EMITIDO', 'qtd_cartao_emitido'))
            prod_db.qtd_lime_ab_conta = to_int(get_col_val(row, 'qtd_lime_ab_conta'))
            prod_db.qtd_lime = to_int(get_col_val(row, 'qtd_lime'))
            prod_db.vlr_lime = to_decimal(get_col_val(row, 'vlr_lime'))
            prod_db.qtd_consignado = to_int(get_col_val(row, 'qtd_consignado'))
            prod_db.vlr_consignado = to_decimal(get_col_val(row, 'vlr_consignado_total', 'vlr_consignado'))
            prod_db.qtd_credito_parcel = to_int(get_col_val(row, 'qtd_credito_parcel'))
            prod_db.vlr_credito_parcel = to_decimal(get_col_val(row, 'VLR_CREDITO_PARCEL', 'vlr_credito_parcel'))
            prod_db.qtd_consorcio = to_int(get_col_val(row, 'qtd_consorcio'))
            prod_db.vlr_ches = to_decimal(get_col_val(row, 'vlr_ches'))
            prod_db.qtd_chesp_contratado = to_int(get_col_val(row, 'qtd_chesp_contratado'))
            prod_db.qtd_microsseguro = to_int(get_col_val(row, 'qtd_microsseguro'))
            prod_db.qtd_super_protegido = to_int(get_col_val(row, 'QTD_SUPER_PROTEGIDO', 'qtd_super_protegido'))
            prod_db.qtd_micro_vivavida = to_int(get_col_val(row, 'QTD_MICRO_VIVAVIDA', 'qtd_micro_vivavida'))
            prod_db.qtd_plano_odonto = to_int(get_col_val(row, 'QTD_PLANO_ODONTO', 'qtd_plano_odonto'))
            prod_db.qtd_seg_residencial = to_int(get_col_val(row, 'QTD_SEG_RESIDENCIAL', 'qtd_seg_residencial'))
            prod_db.qtd_seg_cartao_deb = to_int(get_col_val(row, 'QTD_SEG_CARTAO_DEB', 'qtd_seg_cartao_deb'))
            prod_db.qtd_exp_sorte = to_int(get_col_val(row, 'qtd_exp_sorte'))
            prod_db.vlr_exp_sorte = to_decimal(get_col_val(row, 'VLR_EXP_SORTE', 'vlr_exp_sorte'))

            # Update store metadata if present
            if get_col_val(row, 'STATUS_ANALISE'):
                store_query.status_treinamento = str(get_col_val(row, 'STATUS_ANALISE')).strip()
            if get_col_val(row, 'BLOQUEADO'):
                store_query.status_bloqueio = 'Bloqueado' if str(get_col_val(row, 'BLOQUEADO')).strip().lower() == 'bloqueado' else 'Desbloqueado'
            if get_col_val(row, 'DT_BLOQUEADO'):
                store_query.dt_bloqueado = to_date(get_col_val(row, 'DT_BLOQUEADO'))

        except Exception as err:
            erros_loja.append(f"Loja {ch}: {str(err)}")

    # Finalize status
    if erros_loja:
        new_import.status = 'concluido_com_erros'
        new_import.mensagem_erro = " | ".join(erros_loja[:5])
    else:
        new_import.status = 'concluido'
        
    db.commit()

    if os.path.exists(temp_file_path):
        os.remove(temp_file_path)

    return RedirectResponse("/importar", status_code=303)
