#!/usr/bin/env bash
# =============================================================================
# baixar-playlist.sh — Baixa qualquer playlist ou vídeo do YouTube com yt-dlp
#
# Uso:
#   ./tools/baixar-playlist.sh <URL>                      # vídeo (até Full HD, MP4)
#   ./tools/baixar-playlist.sh <URL> --audio              # apenas áudio em MP3
#   ./tools/baixar-playlist.sh <URL> --listar             # lista os itens, sem baixar
#   ./tools/baixar-playlist.sh <URL> [--audio|--listar] [opções extras do yt-dlp...]
#
# Exemplos:
#   ./tools/baixar-playlist.sh "https://www.youtube.com/playlist?list=PLYAGaV..."
#   ./tools/baixar-playlist.sh "https://www.youtube.com/watch?v=PpS9LfCU-BY" --audio
#   ./tools/baixar-playlist.sh "$URL" --audio --audio-quality 3
#   ./tools/baixar-playlist.sh "$URL" --playlist-items 1-5
#
# Pré-requisitos:
#   1. Ambiente Python / yt-dlp: uv run --project tools/knowledge-extractor python -m yt_dlp
#   2. ffmpeg no PATH ou ~/.local/bin
#   3. (Opcional) cookies.txt na raiz ou nesta pasta para conteúdo restrito
#      Sobrescreva o caminho com a variável COOKIES_FILE, se desejar.
#
# Organização da saída (automática, derivada da própria mídia):
#   download/<Canal>/<Título da Playlist>/NN - <Título>.mp4      (playlists)
#   download/<Canal>/<Título>.mp4                                (vídeo avulso)
#   download/<Canal>/<Título da Playlist> - Áudio/NN - ....mp3   (modo --audio, playlists)
#   download/<Canal>/Áudio/<Título>.mp3                          (modo --audio, avulso)
#   O arquivo baixados-<id>.txt (em download/) evita re-downloads entre execuções.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

DOWNLOAD_DIR="${DOWNLOAD_DIR:-$REPO_ROOT/download}"

# ---------------------------------------------------------------------------
# Parsing flexível: o primeiro argumento que não começa com "-" é a URL;
# --audio e --listar são modos deste script; todo o resto é repassado ao yt-dlp
# ---------------------------------------------------------------------------
URL=""
MODO=""
EXTRA_ARGS=()

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      sed -n '2,26p' "$0"
      exit 0
      ;;
    --audio|--listar)
      MODO="$arg"
      ;;
    -*)
      EXTRA_ARGS+=("$arg")
      ;;
    *)
      if [[ -z "$URL" ]]; then
        URL="$arg"
      else
        EXTRA_ARGS+=("$arg")
      fi
      ;;
  esac
done

if [[ -z "$URL" ]]; then
  echo "ERRO: informe a URL do vídeo ou playlist. Use --help para ver o uso." >&2
  exit 1
fi

# Garante que o ffmpeg estático seja encontrado
export PATH="$HOME/.local/bin:$PATH"

# Localiza o yt-dlp:
# 1. Via uv no pacote tools/knowledge-extractor
# 2. Binário yt-dlp no PATH
# 3. venv local se existir
if command -v uv >/dev/null && [[ -f "$REPO_ROOT/tools/knowledge-extractor/pyproject.toml" ]]; then
  YTDLP_CMD=(uv run --project "$REPO_ROOT/tools/knowledge-extractor" python -m yt_dlp)
elif command -v yt-dlp >/dev/null; then
  YTDLP_CMD=(yt-dlp)
elif [[ -x "$REPO_ROOT/tools/knowledge-extractor/.venv/bin/yt-dlp" ]]; then
  YTDLP_CMD=("$REPO_ROOT/tools/knowledge-extractor/.venv/bin/yt-dlp")
elif [[ -x "$REPO_ROOT/tools/knowledge-extractor/.venv/Scripts/yt-dlp.exe" ]]; then
  YTDLP_CMD=("$REPO_ROOT/tools/knowledge-extractor/.venv/Scripts/yt-dlp.exe")
else
  echo "ERRO: yt-dlp não encontrado. Instale com 'uv add yt-dlp' ou winget." >&2
  exit 1
fi

mkdir -p "$DOWNLOAD_DIR"

if [[ "$MODO" == "--listar" ]]; then
  exec "${YTDLP_CMD[@]}" --flat-playlist \
    --print "%(playlist_index&{:02d} - |)s%(title)s [%(playlist_channel,playlist_uploader,channel,uploader|Canal Desconhecido)s]" \
    ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} "$URL"
fi

# cookies.txt é opcional: necessário apenas para conteúdo restrito
COOKIES_FILE="${COOKIES_FILE:-$REPO_ROOT/cookies.txt}"
if [[ ! -f "$COOKIES_FILE" && -f "$SCRIPT_DIR/cookies.txt" ]]; then
  COOKIES_FILE="$SCRIPT_DIR/cookies.txt"
fi

if [[ -f "$COOKIES_FILE" ]]; then
  COOKIES_ARGS=(--cookies "$COOKIES_FILE")
else
  echo "AVISO: cookies.txt não encontrado — apenas conteúdo público será baixado." >&2
  COOKIES_ARGS=()
fi

# Identifica o canal nos metadados (resiliente com múltiplos fallbacks)
CHANNEL_TMPL="%(playlist_channel,playlist_uploader,channel,uploader|Canal Desconhecido)s"

# Detecta playlist e define template de saída + arquivo de controle por playlist
if [[ "$URL" =~ [\?\&]list=([A-Za-z0-9_-]+) ]]; then
  LIST_ID="${BASH_REMATCH[1]}"
  OUT_VIDEO="$DOWNLOAD_DIR/$CHANNEL_TMPL/%(playlist_title)s/%(playlist_index)02d - %(title)s.%(ext)s"
  OUT_AUDIO="$DOWNLOAD_DIR/$CHANNEL_TMPL/%(playlist_title)s - Áudio/%(playlist_index)02d - %(title)s.%(ext)s"
  ARCHIVE="$DOWNLOAD_DIR/baixados-${LIST_ID}.txt"
else
  OUT_VIDEO="$DOWNLOAD_DIR/$CHANNEL_TMPL/%(title)s.%(ext)s"
  OUT_AUDIO="$DOWNLOAD_DIR/$CHANNEL_TMPL/Áudio/%(title)s.%(ext)s"
  URL_HASH="$(printf '%s' "$URL" | md5sum | cut -c1-8)"
  ARCHIVE="$DOWNLOAD_DIR/baixados-${URL_HASH}.txt"
fi

if [[ "$MODO" == "--audio" ]]; then
  exec "${YTDLP_CMD[@]}" \
    ${COOKIES_ARGS[@]+"${COOKIES_ARGS[@]}"} \
    -x --audio-format mp3 \
    --embed-metadata \
    -o "$OUT_AUDIO" \
    --download-archive "$ARCHIVE" \
    --ignore-errors \
    ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} "$URL"
fi

# Download padrão: vídeo em até Full HD + melhor áudio, mesclado em MP4
exec "${YTDLP_CMD[@]}" \
  ${COOKIES_ARGS[@]+"${COOKIES_ARGS[@]}"} \
  -f "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b" \
  --merge-output-format mp4 \
  --embed-metadata \
  -o "$OUT_VIDEO" \
  --download-archive "$ARCHIVE" \
  --ignore-errors \
  ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} "$URL"
