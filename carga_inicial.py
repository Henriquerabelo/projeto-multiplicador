import os
import sys
import re
import glob
import pandas as pd
import psycopg2
from datetime import datetime, date
from dateutil import parser as dateparser
import msoffcrypto
import io

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')


DB_CONFIG = {
    'host': 'localhost',
    'dbname': 'Multiplicador',
    'user': 'postgres',
    'password': '3325'
}

def get_conn():
    return psycopg2.connect(**DB_CONFIG)

def to_date(valor):
    if pd.isna(valor) or str(valor).strip() in ('', 'nan', 'NaT'):
        return None
    try:
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

def extrair_data_arquivo(nome_arquivo: str) -> date:
    basename = os.path.basename(nome_arquivo)
    match = re.search(r'(\d{4}-\d{2}-\d{2})', basename)
    if not match:
        raise ValueError(f"Nome '{basename}' não contém data no padrão aaaa-mm-dd.")
    return datetime.strptime(match.group(1), "%Y-%m-%d").date()

def main():
    print("🚀 Iniciando carga de dados no banco Multiplicador...")
    conn = get_conn()
    cur = conn.cursor()

    # 1. Carregar Regionais, Vendedores e Lojas iniciais a partir de Multiplicador 2026 08.xlsx
    excel_path = 'Multiplicador 2026 08.xlsx'
    if not os.path.exists(excel_path):
        print(f"❌ Arquivo principal {excel_path} não encontrado!")
        return

    print(f"📂 Carregando dados cadastrais de {excel_path}...")
    
    # Lendo as abas
    df_resp = pd.read_excel(excel_path, sheet_name='Responsavel')
    df_rel = pd.read_excel(excel_path, sheet_name='Rel 2026 08')

    # Garantir que a primeira regional default exista
    cur.execute("INSERT INTO regionais (nome) VALUES ('GERAL') ON CONFLICT (nome) DO UPDATE SET nome = EXCLUDED.nome RETURNING id")
    default_regional_id = cur.fetchone()[0]

    # Inserir regionais encontradas no relatório (filtrando eventuais totais/cálculos numéricos do Excel)
    regionais_set = set(df_rel['ger_regional'].dropna().unique())
    regional_map = {}
    for reg in regionais_set:
        reg_upper = str(reg).strip().upper()
        if not reg_upper or reg_upper.startswith('0.') or reg_upper.replace('.', '', 1).isdigit() or reg_upper in ('NAN', 'NONE', 'NAT'):
            continue
        cur.execute("INSERT INTO regionais (nome) VALUES (%s) ON CONFLICT (nome) DO UPDATE SET nome = EXCLUDED.nome RETURNING id", (reg_upper,))
        regional_map[reg_upper] = cur.fetchone()[0]
    regional_map['GERAL'] = default_regional_id

    # Garantir vendedor Coringa
    cur.execute("INSERT INTO vendedores (nome, regional_id, is_coringa, ativo) VALUES ('SEM RESPONSÁVEL', %s, TRUE, TRUE) ON CONFLICT (nome) DO UPDATE SET nome = EXCLUDED.nome RETURNING id", (default_regional_id,))
    coringa_id = cur.fetchone()[0]

    # Inserir vendedores
    vendedores_set = set(df_resp['responsavel'].dropna().unique())
    vendedor_map = {}
    for vend in vendedores_set:
        vend_upper = str(vend).strip().upper()
        # Encontra a regional predominante para este vendedor
        lojas_vendedor = df_resp[df_resp['responsavel'] == vend]['chave_loja'].map(clean_key)
        reg_vendedor = df_rel[df_rel['chave_loja'].map(clean_key).isin(lojas_vendedor)]['ger_regional'].dropna()
        if not reg_vendedor.empty:
            reg_nome = str(reg_vendedor.iloc[0]).strip().upper()
            reg_id = regional_map.get(reg_nome, default_regional_id)
        else:
            reg_id = default_regional_id

        cur.execute("INSERT INTO vendedores (nome, regional_id, is_coringa, ativo) VALUES (%s, %s, FALSE, TRUE) ON CONFLICT (nome) DO UPDATE SET nome = EXCLUDED.nome RETURNING id", (vend_upper, reg_id))
        vendedor_map[vend_upper] = cur.fetchone()[0]

    print(f"   ✔ Cadastrados {len(vendedor_map)} vendedores e {len(regional_map)} regionais.")

    # Mapear chaves de lojas aos vendedores
    vendedor_por_loja = {}
    for _, row in df_resp.iterrows():
        ch = clean_key(row['chave_loja'])
        resp = str(row['responsavel']).strip().upper()
        vendedor_por_loja[ch] = vendedor_map.get(resp, coringa_id)

    # Inserir/Atualizar Lojas do relatório
    lojas_cadastradas = 0
    for _, row in df_rel.iterrows():
        ch = clean_key(row['chave_loja'])
        if not ch or ch.lower() in ('%', '0', '1', 'nan', 'nat', '', 'none'):
            continue
        nome = str(row['nome_loja']).strip()
        ag_pacb = str(row.get('ag_pacb', '')).strip() or None
        agencia = str(row.get('Agência', '')).strip() or (ag_pacb.split('/')[0] if ag_pacb else '0000')
        uf = str(row.get('uf', 'CE')).strip()[:2].upper()
        municipio = str(row.get('municipio', '')).strip()
        reg_nome = str(row.get('ger_regional', 'GERAL')).strip().upper()
        reg_id = regional_map.get(reg_nome, default_regional_id)

        dt_inst = to_date(row.get('dt_inst_tablet'))
        dt_clube = to_date(row.get('dt_clube'))
        dt_cert = to_date(row.get('dt_certificacao'))
        status_bl = 'Bloqueado' if str(row.get('BLOQUEADO', '')).strip().lower() == 'bloqueado' else 'Desbloqueado'
        dt_bl = to_date(row.get('DT_BLOQUEADO'))
        status_tr = str(row.get('Status', '')).strip() or None

        vendedor_id = vendedor_por_loja.get(ch, coringa_id)

        cur.execute("""
            INSERT INTO lojas (
                chave_loja, agencia, nome_loja, ag_pacb, uf, municipio, regional_id,
                dt_inst_tablet, dt_clube, dt_certificacao, status_bloqueio, dt_bloqueado,
                status_treinamento, ativo, vendedor_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
            ON CONFLICT (chave_loja) DO UPDATE SET
                agencia = EXCLUDED.agencia,
                nome_loja = EXCLUDED.nome_loja,
                ag_pacb = EXCLUDED.ag_pacb,
                uf = EXCLUDED.uf,
                municipio = EXCLUDED.municipio,
                regional_id = EXCLUDED.regional_id,
                dt_inst_tablet = EXCLUDED.dt_inst_tablet,
                dt_clube = EXCLUDED.dt_clube,
                dt_certificacao = EXCLUDED.dt_certificacao,
                status_bloqueio = EXCLUDED.status_bloqueio,
                dt_bloqueado = EXCLUDED.dt_bloqueado,
                status_treinamento = EXCLUDED.status_treinamento,
                vendedor_id = EXCLUDED.vendedor_id,
                atualizado_em = NOW()
        """, (ch, agencia, nome, ag_pacb, uf, municipio, reg_id, dt_inst, dt_clube, dt_cert, status_bl, dt_bl, status_tr, vendedor_id))
        lojas_cadastradas += 1

    print(f"   ✔ Cadastradas {lojas_cadastradas} lojas a partir da planilha principal.")

    # 2. Carregar arquivos diários
    dados_diarios_dir = 'Dados-diarios'
    arquivos = glob.glob(os.path.join(dados_diarios_dir, '*'))
    # Ordenar por nome de arquivo (para processar cronologicamente)
    arquivos.sort()

    print(f"\n📂 Encontrados {len(arquivos)} arquivos diários para processamento.")

    for filepath in arquivos:
        filename = os.path.basename(filepath)
        if filename.startswith('Multiplicador'):
            print(f"   ℹ Pulando arquivo de planilha consolidada: {filename}")
            continue

        try:
            data_file = extrair_data_arquivo(filename)
        except Exception as e:
            print(f"   ⚠️ Ignorando arquivo sem data no padrão: {filename}")
            continue

        ref_mes = data_file.replace(day=1)

        print(f"   ⏳ Processando {filename} (Referência: {ref_mes}) ...")

        # Tratamento especial para xls possivelmente criptografado
        df_diario = None
        if filepath.endswith('.xls'):
            # Tenta decodificar se for criptografado
            try:
                with open(filepath, 'rb') as f:
                    office_file = msoffcrypto.OfficeFile(f)
                    if office_file.is_encrypted():
                        # Tentamos com senhas se tivéssemos, senão reportamos erro
                        print(f"   ❌ {filename} está criptografado e não pôde ser aberto.")
                        continue
                df_diario = pd.read_excel(filepath)
            except Exception as e:
                print(f"   ❌ Erro ao abrir XLS {filename}: {e}")
                continue
        elif filepath.endswith('.xlsx'):
            try:
                df_diario = pd.read_excel(filepath)
            except Exception as e:
                print(f"   ❌ Erro ao abrir XLSX {filename}: {e}")
                continue
        elif filepath.endswith('.csv'):
            try:
                with open(filepath, 'r', encoding='cp1252') as f:
                    header_cols = f.readline().strip().split(';')
                num_cols = len([c for c in header_cols if c.strip()])
                df_diario = pd.read_csv(filepath, sep=';', encoding='cp1252', dtype=str, usecols=range(num_cols))
                # Limpa colunas em branco extras se houver
                df_diario.columns = [c.strip() for c in df_diario.columns]
            except Exception as e:
                print(f"   ❌ Erro ao abrir CSV {filename}: {e}")
                continue

        if df_diario is None or df_diario.empty:
            print(f"   ⚠️ Arquivo vazio ou ilegível: {filename}")
            continue

        # Normaliza nomes de colunas
        col_mapping = {c.lower().replace(' ', '_'): c for c in df_diario.columns}
        
        def get_col_val(row, *possible_names):
            for name in possible_names:
                normalized = name.lower().replace(' ', '_')
                if normalized in col_mapping:
                    return row[col_mapping[normalized]]
            return None

        # Registrar importação
        cur.execute("""
            INSERT INTO importacoes (referencia, data_arquivo, arquivo_nome, total_lojas, status)
            VALUES (%s, %s, %s, %s, 'processando')
            ON CONFLICT (referencia, data_arquivo) DO UPDATE SET
                status = 'processando',
                importado_em = NOW()
            RETURNING id
        """, (ref_mes, data_file, filename, len(df_diario)))
        import_id = cur.fetchone()[0]

        lojas_importadas = 0
        erros_loja = []

        # Processar linhas do arquivo diário
        for idx, row in df_diario.iterrows():
            ch = clean_key(get_col_val(row, 'chave_loja', 'Chave loja'))
            if not ch or ch.lower() in ('%', '0', '1', 'nan', 'nat', '', 'none'):
                continue

            # Buscar loja no banco
            cur.execute("SELECT id, vendedor_id FROM lojas WHERE chave_loja = %s", (ch,))
            res_loja = cur.fetchone()

            if not res_loja:
                # Ignore daily production if store is not in the active cadastral list
                continue

            loja_id, v_id = res_loja[0], res_loja[1]

            # Elegibilidade
            elegivel = normalizar_elegivel(
                get_col_val(row, 'BLOQUEADO'),
                get_col_val(row, 'REFERENCIA')
            )

            try:
                cur.execute("""
                    INSERT INTO producao_lojas (
                        loja_id, importacao_id, vendedor_id, referencia, elegivel, data_ult_transacao,
                        qtd_trx_contabil, qtd_contas, qtd_contas_com_deposito, qtd_cesta_serv,
                        qtd_contas_pj, qtd_contas_folha, qtd_mobilidade, qtd_mtoken,
                        qtd_cartao_emitido, qtd_lime_ab_conta, qtd_lime, vlr_lime,
                        qtd_consignado, vlr_consignado, qtd_credito_parcel, vlr_credito_parcel, qtd_consorcio,
                        vlr_ches, qtd_chesp_contratado, qtd_microsseguro, qtd_super_protegido,
                        qtd_micro_vivavida, qtd_plano_odonto, qtd_seg_residencial, qtd_seg_cartao_deb,
                        qtd_exp_sorte, vlr_exp_sorte
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s
                    ) ON CONFLICT (loja_id, referencia) DO UPDATE SET
                        importacao_id = EXCLUDED.importacao_id,
                        vendedor_id = EXCLUDED.vendedor_id,
                        elegivel = EXCLUDED.elegivel,
                        data_ult_transacao = EXCLUDED.data_ult_transacao,
                        qtd_trx_contabil = EXCLUDED.qtd_trx_contabil,
                        qtd_contas = EXCLUDED.qtd_contas,
                        qtd_contas_com_deposito = EXCLUDED.qtd_contas_com_deposito,
                        qtd_cesta_serv = EXCLUDED.qtd_cesta_serv,
                        qtd_contas_pj = EXCLUDED.qtd_contas_pj,
                        qtd_contas_folha = EXCLUDED.qtd_contas_folha,
                        qtd_mobilidade = EXCLUDED.qtd_mobilidade,
                        qtd_mtoken = EXCLUDED.qtd_mtoken,
                        qtd_cartao_emitido = EXCLUDED.qtd_cartao_emitido,
                        qtd_lime_ab_conta = EXCLUDED.qtd_lime_ab_conta,
                        qtd_lime = EXCLUDED.qtd_lime,
                        vlr_lime = EXCLUDED.vlr_lime,
                        qtd_consignado = EXCLUDED.qtd_consignado,
                        vlr_consignado = EXCLUDED.vlr_consignado,
                        qtd_credito_parcel = EXCLUDED.qtd_credito_parcel,
                        vlr_credito_parcel = EXCLUDED.vlr_credito_parcel,
                        qtd_consorcio = EXCLUDED.qtd_consorcio,
                        vlr_ches = EXCLUDED.vlr_ches,
                        qtd_chesp_contratado = EXCLUDED.qtd_chesp_contratado,
                        qtd_microsseguro = EXCLUDED.qtd_microsseguro,
                        qtd_super_protegido = EXCLUDED.qtd_super_protegido,
                        qtd_micro_vivavida = EXCLUDED.qtd_micro_vivavida,
                        qtd_plano_odonto = EXCLUDED.qtd_plano_odonto,
                        qtd_seg_residencial = EXCLUDED.qtd_seg_residencial,
                        qtd_seg_cartao_deb = EXCLUDED.qtd_seg_cartao_deb,
                        qtd_exp_sorte = EXCLUDED.qtd_exp_sorte,
                        vlr_exp_sorte = EXCLUDED.vlr_exp_sorte,
                        atualizado_em = NOW()
                """, (
                    loja_id, import_id, v_id, ref_mes, elegivel, to_date(get_col_val(row, 'data_ult_transacao')),
                    to_int(get_col_val(row, 'qtd_TrxContabil', 'qtd_trx_contabil')),
                    to_int(get_col_val(row, 'qtd_contas')),
                    to_int(get_col_val(row, 'qtd_contas_com_deposito')),
                    to_int(get_col_val(row, 'qtd_cesta_serv')),
                    to_int(get_col_val(row, 'QTD_CONTAS_PJ', 'qtd_contas_pj')),
                    to_int(get_col_val(row, 'QTD_CONTAS_FOLHA', 'qtd_contas_folha')),
                    to_int(get_col_val(row, 'QTD_MOBILIDADE', 'qtd_mobilidade')),
                    to_int(get_col_val(row, 'QTD_MTOKEN', 'qtd_mtoken')),
                    to_int(get_col_val(row, 'QTD_CARTAO_EMITIDO', 'qtd_cartao_emitido')),
                    to_int(get_col_val(row, 'qtd_lime_ab_conta')),
                    to_int(get_col_val(row, 'qtd_lime')),
                    to_decimal(get_col_val(row, 'vlr_lime')),
                    to_int(get_col_val(row, 'qtd_consignado')),
                    to_decimal(get_col_val(row, 'vlr_consignado', 'vlr_consignado_total')),
                    to_int(get_col_val(row, 'QTD_CREDITO_PARCEL_DTLHES', 'qtd_credito_parcel')),
                    to_decimal(get_col_val(row, 'VLR_CREDITO_PARCEL', 'vlr_credito_parcel')),
                    to_int(get_col_val(row, 'qtd_consorcio')),
                    to_decimal(get_col_val(row, 'vlr_ches')),
                    to_int(get_col_val(row, 'qtd_chesp_contratado')),
                    to_int(get_col_val(row, 'qtd_microsseguro')),
                    to_int(get_col_val(row, 'QTD_SUPER_PROTEGIDO', 'qtd_super_protegido')),
                    to_int(get_col_val(row, 'QTD_MICRO_VIVAVIDA', 'qtd_micro_vivavida')),
                    to_int(get_col_val(row, 'QTD_PLANO_ODONTO', 'qtd_plano_odonto')),
                    to_int(get_col_val(row, 'QTD_SEG_RESIDENCIAL', 'qtd_seg_residencial')),
                    to_int(get_col_val(row, 'QTD_SEG_CARTAO_DEB', 'qtd_seg_cartao_deb')),
                    to_int(get_col_val(row, 'QTD_EXP_SORTE', 'qtd_exp_sorte')),
                    to_decimal(get_col_val(row, 'VLR_EXP_SORTE', 'vlr_exp_sorte'))
                ))
                lojas_importadas += 1
            except Exception as e:
                erros_loja.append(f"Loja {ch}: {e}")

        status_imp = 'concluido' if not erros_loja else 'erro'
        erro_msg = '\n'.join(erros_loja[:10]) if erros_loja else None
        
        cur.execute("UPDATE importacoes SET status = %s, mensagem_erro = %s WHERE id = %s", (status_imp, erro_msg, import_id))
        conn.commit()

        print(f"   ✔ Sucesso: {lojas_importadas} lojas carregadas de {filename}. Erros: {len(erros_loja)}")

    cur.close()
    conn.close()
    print("\n🎉 Processo de carga inicial finalizado com sucesso!")

if __name__ == '__main__':
    main()
