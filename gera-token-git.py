import jwt
import time
import requests
import argparse
from pathlib import Path

def generate_github_token(app_id, installation_id, private_key_path):
    """
    Gera um token de acesso do GitHub usando App ID, Installation ID e Private Key
    Retorna: token de acesso (formato ghs_...) ou None em caso de erro
    """
    try:
        # Ler a chave privada
        private_key = Path(private_key_path).read_text()

        # Gerar JWT
        now = int(time.time())
        payload = {
            "iat": now - 60,  # Emitido há 60 segundos (permite pequeno drift de relógio)
            "exp": now + (9 * 60),  # Expira em 9 minutos (máximo 10 minutos)
            "iss": app_id
        }

        jwt_token = jwt.encode(payload, private_key, algorithm="RS256")

        # Se jwt.encode retornar bytes, converter para string
        if isinstance(jwt_token, bytes):
            jwt_token = jwt_token.decode('utf-8')

        # Obter token de acesso da instalação
        url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }

        response = requests.post(url, headers=headers)
        response.raise_for_status()  # Levanta exceção para códigos de erro HTTP

        token_data = response.json()
        return token_data["token"]

    except Exception as e:
        print(f"Erro ao gerar token: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description='Gerar token de acesso do GitHub')
    parser.add_argument('--app-id', required=True, help='GitHub App ID')
    parser.add_argument('--installation-id', required=True, help='GitHub Installation ID')
    parser.add_argument('--private-key', required=True, help='Caminho para o arquivo da chave privada')

    args = parser.parse_args()

    # Gerar o token
    access_token = generate_github_token(
        app_id=args.app_id,
        installation_id=args.installation_id,
        private_key_path=args.private_key
    )

    if access_token:
        print(f"Token gerado com sucesso: {access_token}")
        print("\nExemplo de uso com curl:")
        print(f'curl -H "Authorization: Bearer {access_token}" https://api.github.com/user')
    else:
        print("Falha ao gerar token")
        exit(1)

if __name__ == "__main__":
    main()
