from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import date
from typing import List, Optional
import uuid

import models

def get_user_by_login(db: Session, login: str) -> Optional[models.Usuario]:
    login_clean = login.strip().lower()
    return db.query(models.Usuario).filter(
        (models.Usuario.email.ilike(login_clean)) | (models.Usuario.nome.ilike(login_clean)),
        models.Usuario.ativo == True
    ).first()

def get_available_months(db: Session):
    query = text("SELECT DISTINCT referencia FROM importacoes ORDER BY referencia DESC")
    result = db.execute(query).fetchall()
    return [row[0] for row in result]

def get_ranking(db: Session, ref_date: date, allowed_vendedor_ids: Optional[List[uuid.UUID]] = None):
    sql = """
        SELECT posicao, vendedor_id, vendedor, regional, total_lojas, 
               lojas_elegiveis1, lojas_elegiveis2, 
               pontuacao1_total, pontuacao2_total, 
               pontuacao1_media, pontuacao2_media
        FROM vw_ranking_vendedor
        WHERE referencia = :ref
    """
    params = {"ref": ref_date}
    if allowed_vendedor_ids is not None:
        if not allowed_vendedor_ids:
            return []
        sql += " AND vendedor_id::text = ANY(:vids)"
        params["vids"] = [str(vid) for vid in allowed_vendedor_ids]
        
    sql += " ORDER BY posicao ASC"
    result = db.execute(text(sql), params).fetchall()
    
    ranking_list = [dict(row._mapping) for row in result]
    if allowed_vendedor_ids is not None:
        for idx, item in enumerate(ranking_list, start=1):
            item['posicao'] = idx
            
    return ranking_list

def get_vendedor_details(db: Session, ref_date: date, vendedor_id: str):
    query = text("""
        SELECT * FROM vw_pontuacao
        WHERE referencia = :ref AND vendedor_id = :vendedor_id
        ORDER BY pontuacao2 DESC, nome_loja ASC
    """)
    result = db.execute(query, {"ref": ref_date, "vendedor_id": uuid.UUID(vendedor_id)}).fetchall()
    return [dict(row._mapping) for row in result]

def get_enquadramento_detalhado(db: Session, ref_date: date, allowed_vendedor_ids: Optional[List[uuid.UUID]] = None):
    sql = """
        SELECT 
            vw.chave_loja,
            vw.nome_loja,
            vw.municipio,
            vw.uf,
            vw.agencia,
            vw.ag_pacb,
            vw.regional,
            vw.vendedor,
            l.status_treinamento,
            vw.status_bloqueio,
            vw.elegivel1,
            vw.pontuacao1,
            vw.elegivel2,
            vw.pontuacao2,
            COALESCE(p.qtd_trx_contabil, 0) as qtd_trx_contabil,
            COALESCE(p.qtd_contas, 0) as qtd_contas,
            COALESCE(p.qtd_cesta_serv, 0) as qtd_cesta_serv,
            (COALESCE(p.vlr_lime, 0) + COALESCE(p.vlr_consignado, 0) + COALESCE(p.vlr_credito_parcel, 0) + COALESCE(p.vlr_ches, 0)) as vlr_credito_total,
            (COALESCE(p.qtd_microsseguro, 0) + COALESCE(p.qtd_super_protegido, 0) + COALESCE(p.qtd_micro_vivavida, 0) + COALESCE(p.qtd_plano_odonto, 0) + COALESCE(p.qtd_seg_residencial, 0) + COALESCE(p.qtd_seg_cartao_deb, 0)) as qtd_seguros_total,
            COALESCE(p.qtd_consorcio, 0) as qtd_consorcio,
            COALESCE(p.qtd_cartao_emitido, 0) as qtd_cartao_emitido
        FROM vw_pontuacao vw
        JOIN lojas l ON l.id = vw.loja_id
        LEFT JOIN producao_lojas p ON p.loja_id = vw.loja_id AND p.referencia = vw.referencia
        WHERE vw.referencia = :ref
    """
    params = {"ref": ref_date}
    if allowed_vendedor_ids is not None:
        if not allowed_vendedor_ids:
            return []
        sql += " AND vw.vendedor_id::text = ANY(:vids)"
        params["vids"] = [str(vid) for vid in allowed_vendedor_ids]

    sql += " ORDER BY vw.regional ASC, vw.vendedor ASC, vw.pontuacao2 DESC, vw.nome_loja ASC"
    result = db.execute(text(sql), params).fetchall()
    return [dict(row._mapping) for row in result]

def get_stores_list(db: Session, search: str = None, vendedor_id: str = None, allowed_vendedor_ids: Optional[List[uuid.UUID]] = None):
    sql = """
        SELECT l.id, l.chave_loja, l.nome_loja, l.ag_pacb, l.uf, l.municipio, 
               r.nome as regional, v.id as vendedor_id, v.nome as vendedor,
               last_imp.data_arquivo as ultima_data
        FROM lojas l
        JOIN regionais r ON r.id = l.regional_id
        LEFT JOIN vendedores v ON v.id = l.vendedor_id
        LEFT JOIN LATERAL (
            SELECT i.data_arquivo
            FROM producao_lojas p
            JOIN importacoes i ON i.id = p.importacao_id
            WHERE p.loja_id = l.id
            ORDER BY i.data_arquivo DESC
            LIMIT 1
        ) last_imp ON TRUE
        WHERE l.ativo = TRUE
    """
    params = {}
    if search:
        sql += " AND (l.nome_loja ILIKE :search OR l.chave_loja ILIKE :search OR l.municipio ILIKE :search)"
        params["search"] = f"%{search}%"
    if vendedor_id:
        sql += " AND l.vendedor_id = :vendedor_id"
        params["vendedor_id"] = uuid.UUID(vendedor_id)
    elif allowed_vendedor_ids is not None:
        if not allowed_vendedor_ids:
            return []
        sql += " AND l.vendedor_id::text = ANY(:allowed_vids)"
        params["allowed_vids"] = [str(vid) for vid in allowed_vendedor_ids]
        
    sql += " ORDER BY l.nome_loja ASC"
    result = db.execute(text(sql), params).fetchall()
    return [dict(row._mapping) for row in result]

def update_store_vendedor(db: Session, store_id: str, vendedor_id: str):
    sql = text("UPDATE lojas SET vendedor_id = :vendedor_id, atualizado_em = NOW() WHERE id = :store_id")
    v_id = uuid.UUID(vendedor_id) if vendedor_id else None
    db.execute(sql, {"vendedor_id": v_id, "store_id": uuid.UUID(store_id)})
    db.commit()

def get_vendedores(db: Session, allowed_vendedor_ids: Optional[List[uuid.UUID]] = None):
    sql = """
        SELECT v.id, v.nome, r.nome as regional, v.is_coringa, v.ativo, c.nome as coordenador
        FROM vendedores v 
        JOIN regionais r ON r.id = v.regional_id 
        LEFT JOIN vendedores c ON c.id = v.coordenador_id
        WHERE 1=1
    """
    params = {}
    if allowed_vendedor_ids is not None:
        if not allowed_vendedor_ids:
            return []
        sql += " AND v.id::text = ANY(:allowed_vids)"
        params["allowed_vids"] = [str(vid) for vid in allowed_vendedor_ids]
        
    sql += " ORDER BY v.is_coringa DESC, v.nome ASC"
    result = db.execute(text(sql), params).fetchall()
    return [dict(row._mapping) for row in result]

def create_vendedor(db: Session, nome: str, regional_id: str, coordenador_id: Optional[str] = None):
    new_id = uuid.uuid4()
    sql = text("""
        INSERT INTO vendedores (id, nome, regional_id, coordenador_id, ativo, is_coringa, criado_em)
        VALUES (:id, :nome, :regional_id, :coordenador_id, TRUE, FALSE, NOW())
    """)
    db.execute(sql, {
        "id": new_id, 
        "nome": nome.upper().strip(), 
        "regional_id": uuid.UUID(regional_id),
        "coordenador_id": uuid.UUID(coordenador_id) if coordenador_id else None
    })
    db.commit()
    return new_id

def get_regionais(db: Session):
    sql = text("SELECT id, nome FROM regionais ORDER BY nome ASC")
    result = db.execute(sql).fetchall()
    return [dict(row._mapping) for row in result]

def get_importacoes(db: Session):
    sql = text("SELECT id, referencia, data_arquivo, arquivo_nome, total_lojas, status, mensagem_erro, importado_em FROM importacoes ORDER BY data_arquivo DESC, importado_em DESC LIMIT 20")
    result = db.execute(sql).fetchall()
    return [dict(row._mapping) for row in result]

def get_ultima_importacao(db: Session):
    sql = text("""
        SELECT id, referencia, data_arquivo, arquivo_nome, total_lojas, status, mensagem_erro, importado_em 
        FROM importacoes 
        WHERE status IN ('concluido', 'concluido_com_erros')
        ORDER BY data_arquivo DESC, importado_em DESC 
        LIMIT 1
    """)
    result = db.execute(sql).fetchone()
    if result:
        return dict(result._mapping)
    # Fallback to any latest import if none marked as completed
    sql_fallback = text("""
        SELECT id, referencia, data_arquivo, arquivo_nome, total_lojas, status, mensagem_erro, importado_em 
        FROM importacoes 
        ORDER BY data_arquivo DESC, importado_em DESC 
        LIMIT 1
    """)
    res_fallback = db.execute(sql_fallback).fetchone()
    return dict(res_fallback._mapping) if res_fallback else None
