import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Date, Numeric, Integer, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()

class Regional(Base):
    __tablename__ = 'regionais'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nome = Column(String(255), unique=True, nullable=False)
    criado_em = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    
    vendedores = relationship("Vendedor", back_populates="regional")
    lojas = relationship("Loja", back_populates="regional")

class Vendedor(Base):
    __tablename__ = 'vendedores'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nome = Column(String(255), unique=True, nullable=False)
    usuario_id = Column(UUID(as_uuid=True), ForeignKey('usuarios.id', ondelete='SET NULL'), nullable=True)
    regional_id = Column(UUID(as_uuid=True), ForeignKey('regionais.id'), nullable=False)
    ativo = Column(Boolean, default=True, nullable=False)
    coordenador_id = Column(UUID(as_uuid=True), ForeignKey('vendedores.id', ondelete='SET NULL'), nullable=True)
    criado_em = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    is_coringa = Column(Boolean, default=False, nullable=False)
    
    regional = relationship("Regional", back_populates="vendedores")
    lojas = relationship("Loja", back_populates="vendedor")
    producoes = relationship("ProducaoLoja", back_populates="vendedor")

class Loja(Base):
    __tablename__ = 'lojas'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chave_loja = Column(String(255), unique=True, nullable=False)
    agencia = Column(String(255), nullable=False)
    nome_loja = Column(String(255), nullable=False)
    ag_pacb = Column(String(255), nullable=True)
    van = Column(String(255), nullable=True)
    uf = Column(String(2), nullable=False)
    municipio = Column(String(255), nullable=False)
    regional_id = Column(UUID(as_uuid=True), ForeignKey('regionais.id'), nullable=False)
    dt_inst_tablet = Column(Date, nullable=True)
    dt_clube = Column(Date, nullable=True)
    dt_certificacao = Column(Date, nullable=True)
    status_bloqueio = Column(String(50), default='Desbloqueado', nullable=False)
    dt_bloqueado = Column(Date, nullable=True)
    status_treinamento = Column(String(255), nullable=True)
    ativo = Column(Boolean, default=True, nullable=False)
    vendedor_id = Column(UUID(as_uuid=True), ForeignKey('vendedores.id', ondelete='SET NULL'), nullable=True)
    criado_em = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    atualizado_em = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    regional = relationship("Regional", back_populates="lojas")
    vendedor = relationship("Vendedor", back_populates="lojas")
    producoes = relationship("ProducaoLoja", back_populates="loja")

class Importacao(Base):
    __tablename__ = 'importacoes'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), ForeignKey('usuarios.id', ondelete='SET NULL'), nullable=True)
    referencia = Column(Date, nullable=False)
    data_arquivo = Column(Date, nullable=False)
    arquivo_nome = Column(String(255), nullable=False)
    total_lojas = Column(Integer, nullable=True)
    status = Column(String(50), default='processando', nullable=False)
    mensagem_erro = Column(Text, nullable=True)
    importado_em = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    
    producoes = relationship("ProducaoLoja", back_populates="importacao")

class ProducaoLoja(Base):
    __tablename__ = 'producao_lojas'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    loja_id = Column(UUID(as_uuid=True), ForeignKey('lojas.id'), nullable=False)
    importacao_id = Column(UUID(as_uuid=True), ForeignKey('importacoes.id'), nullable=False)
    vendedor_id = Column(UUID(as_uuid=True), ForeignKey('vendedores.id', ondelete='SET NULL'), nullable=True)
    referencia = Column(Date, nullable=False)
    elegivel = Column(String(50), nullable=True)
    data_ult_transacao = Column(Date, nullable=True)
    qtd_trx_contabil = Column(Integer, default=0, nullable=False)
    qtd_contas = Column(Integer, default=0, nullable=False)
    qtd_contas_com_deposito = Column(Integer, default=0, nullable=False)
    qtd_cesta_serv = Column(Integer, default=0, nullable=False)
    qtd_contas_pj = Column(Integer, default=0, nullable=False)
    qtd_contas_folha = Column(Integer, default=0, nullable=False)
    qtd_mobilidade = Column(Integer, default=0, nullable=False)
    qtd_mtoken = Column(Integer, default=0, nullable=False)
    qtd_cartao_emitido = Column(Integer, default=0, nullable=False)
    qtd_lime_ab_conta = Column(Integer, default=0, nullable=False)
    qtd_lime = Column(Integer, default=0, nullable=False)
    vlr_lime = Column(Numeric, default=0.0, nullable=False)
    qtd_consignado = Column(Integer, default=0, nullable=False)
    vlr_consignado = Column(Numeric, default=0.0, nullable=False)
    qtd_credito_parcel = Column(Integer, default=0, nullable=False)
    vlr_credito_parcel = Column(Numeric, default=0.0, nullable=False)
    qtd_consorcio = Column(Integer, default=0, nullable=False)
    vlr_ches = Column(Numeric, default=0.0, nullable=False)
    qtd_chesp_contratado = Column(Integer, default=0, nullable=False)
    qtd_microsseguro = Column(Integer, default=0, nullable=False)
    qtd_super_protegido = Column(Integer, default=0, nullable=False)
    qtd_micro_vivavida = Column(Integer, default=0, nullable=False)
    qtd_plano_odonto = Column(Integer, default=0, nullable=False)
    qtd_seg_residencial = Column(Integer, default=0, nullable=False)
    qtd_seg_cartao_deb = Column(Integer, default=0, nullable=False)
    qtd_exp_sorte = Column(Integer, default=0, nullable=False)
    vlr_exp_sorte = Column(Numeric, default=0.0, nullable=False)
    criado_em = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    atualizado_em = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    loja = relationship("Loja", back_populates="producoes")
    importacao = relationship("Importacao", back_populates="producoes")
    vendedor = relationship("Vendedor", back_populates="producoes")

class Usuario(Base):
    __tablename__ = 'usuarios'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nome = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    senha_hash = Column(Text, nullable=False)
    role = Column(String(50), default='vendedor', nullable=False)
    vendedor_id = Column(UUID(as_uuid=True), ForeignKey('vendedores.id', ondelete='SET NULL'), nullable=True)
    ativo = Column(Boolean, default=True, nullable=False)
    senha_alterada_em = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    primeiro_acesso = Column(Boolean, default=True, nullable=False)
    criado_em = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    atualizado_em = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    vendedor = relationship("Vendedor", foreign_keys=[vendedor_id])
