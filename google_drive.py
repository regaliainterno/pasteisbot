# google_drive.py
import os
import pandas as pd
import pickle
import json
import base64
import io

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

import config  # Importa nossas configurações

SCOPES = ['https://www.googleapis.com/auth/drive']


def get_drive_service():
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if os.path.exists('credentials.json'):
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                google_token_base_64 = os.environ.get('GOOGLE_TOKEN_BASE_64')
                google_creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
                if google_token_base_64 and google_creds_json:
                    try:
                        decoded_token = base64.b64decode(google_token_base_64)
                        creds = pickle.loads(decoded_token)
                        if not creds.valid and creds.refresh_token:
                            print("Token do Google expirado. Tentando renovar...")
                            creds.refresh(Request())
                            print("Token renovado com sucesso.")
                    except Exception as e:
                        print(f"Erro ao processar credenciais do Google: {e}")
                        raise ValueError(f"Erro ao processar credenciais: {e}")
                else:
                    raise ValueError("Token ou credenciais não encontrados.")

        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    return build('drive', 'v3', credentials=creds)


def get_file_id(service, file_name, folder_id):
    query = f"name='{file_name}' and trashed=false"
    if folder_id: query += f" and '{folder_id}' in parents"
    response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    files = response.get('files', [])
    return files[0]['id'] if files else None


def download_dataframe(service, file_name, file_id, default_cols):
    if not file_id:
        df = pd.DataFrame(columns=default_cols)
        if default_cols:
            df[default_cols[0]] = pd.to_datetime(df[default_cols[0]], utc=True)
        return df

    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done: status, done = downloader.next_chunk()
    fh.seek(0)

    try:
        df = pd.read_csv(fh)
        if df.empty:
            df = pd.DataFrame(columns=default_cols)
            if default_cols: df[default_cols[0]] = pd.to_datetime(df[default_cols[0]], utc=True)
            return df

        df[df.columns[0]] = pd.to_datetime(df[df.columns[0]], utc=True)

        if file_name == config.DRIVE_VENDAS_FILE and 'lucro_venda' not in df.columns:
            df['custo_unidade'] = config.PRECO_FIXO_CUSTO
            df['lucro_venda'] = df['total_venda'] - (df['quantidade'] * config.PRECO_FIXO_CUSTO)
        return df
    except (pd.errors.EmptyDataError, KeyError, IndexError):
        df = pd.DataFrame(columns=default_cols)
        if default_cols:
            df[default_cols[0]] = pd.to_datetime(df[default_cols[0]], utc=True)
        return df


def upload_dataframe(service, df, file_name, file_id, folder_id):
    csv_bytes = df.to_csv(index=False).encode('utf-8')
    fh = io.BytesIO(csv_bytes)
    media = MediaIoBaseUpload(fh, mimetype='text/csv', resumable=True)
    file_metadata = {'name': file_name}
    if folder_id and not file_id: file_metadata['parents'] = [folder_id]

    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
    else:
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()