#!/bin/zsh
# Construit Rature.app (autonome : Python, numpy, whisper.cpp Metal embarqués) et l'installe dans /Applications.
# Usage : ./construire.sh            (ajouter --sans-installer pour laisser l'app dans dist/)
#
# Produit aussi, dans ../publication/<version>/ : Rature-<version>.dmg et version.json (à publier sur kirea.fr/rature/).
# Signature Apple (facultative, nécessite un compte Apple Developer) :
#   RATURE_IDENTITE="Developer ID Application: Nom (ÉQUIPE)"   certificat installé dans le trousseau
#   RATURE_NOTAIRE="profil"     profil créé une fois avec : xcrun notarytool store-credentials profil
# Adresse de téléchargement du .dmg (par défaut sur kirea.fr) : RATURE_URL_DMG="https://…/" 
set -euo pipefail

SRC="${0:A:h}"
BUILD="$SRC/../build"
VENV="$BUILD/venv"
WHISPER_VERSION="v1.8.3"
VERSION=$(sed -n "s/^VERSION = '\(.*\)'/\1/p" "$SRC/rature.py")
# Les outils en ligne de commande d'Apple suffisent (pas besoin d'accepter la licence Xcode)
export DEVELOPER_DIR=/Library/Developer/CommandLineTools

mkdir -p "$BUILD"
cd "$BUILD"

echo "▸ Environnement Python"
# Python « standalone » (compilé pour macOS 11+) plutôt que celui de Homebrew (lié à la version de macOS
# de cette machine) : l'app reste utilisable sur des Mac plus anciens.
if [[ ! -x "$VENV/bin/python" ]]; then
  uv python install 3.13
  uv venv -q --python "$(uv python find 3.13 --managed-python)" "$VENV"
  VIRTUAL_ENV="$VENV" uv pip install -q numpy pywebview pyinstaller certifi
fi
# MLX + mlx-speech : modèle Cohere Transcribe (GPU Apple)
"$VENV/bin/python" -c "import mlx_speech" 2> /dev/null || VIRTUAL_ENV="$VENV" uv pip install -q mlx-speech
# version de MLX compilée pour macOS 14 (sinon pip prend celle du macOS de cette machine)
MLX=$("$VENV/bin/python" -c "import importlib.metadata as m; print(m.version('mlx'))")
VIRTUAL_ENV="$VENV" MACOSX_DEPLOYMENT_TARGET=14.0 uv pip install -q --python-platform aarch64-apple-darwin \
  --reinstall-package mlx --reinstall-package mlx-metal "mlx==$MLX" "mlx-metal==$MLX"

echo "▸ whisper.cpp $WHISPER_VERSION (Metal, binaire statique)"
if [[ ! -x whisper.cpp/build/bin/whisper-cli ]]; then
  [[ -d whisper.cpp ]] || git clone -q --depth 1 --branch "$WHISPER_VERSION" https://github.com/ggml-org/whisper.cpp.git
  cmake -S whisper.cpp -B whisper.cpp/build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
    -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON -DGGML_NATIVE=OFF -DGGML_BLAS=OFF \
    -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF -DWHISPER_SDL2=OFF -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0 > cmake.log
  cmake --build whisper.cpp/build -j 10 --target whisper-cli > build.log
fi

echo "▸ LAME (encodage MP3) et RNNoise (réduction du bruit)"
if [[ ! -x lame-3.100/frontend/lame ]]; then
  if [[ ! -d lame-3.100 ]]; then
    curl -sSL --fail -o lame-3.100.tar.gz https://downloads.sourceforge.net/project/lame/lame/3.100/lame-3.100.tar.gz
    echo "ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e  lame-3.100.tar.gz" | shasum -a 256 -c -
    tar xzf lame-3.100.tar.gz
  fi
  (cd lame-3.100 && MACOSX_DEPLOYMENT_TARGET=13.0 ./configure --disable-shared --enable-static --disable-gtktest CFLAGS=-O2 > ../lame-configure.log \
    && make -j10 > ../lame-build.log)
fi
if [[ ! -x rnnoise/rnnoise ]]; then
  [[ -d rnnoise ]] || git clone -q --depth 1 --branch v0.1.1 https://github.com/xiph/rnnoise.git
  (cd rnnoise && cc -O3 -mmacosx-version-min=13.0 -Iinclude src/*.c examples/rnnoise_demo.c -lm -o rnnoise 2> /dev/null)
fi

echo "▸ FFmpeg, version audio seule (le son des MKV, AVI, WebM… que macOS ne sait pas lire) — LGPL"
FFV=9.0.2
if [[ -z "${RATURE_SANS_FFMPEG:-}" && ! -x ffmpeg-$FFV/ffmpeg ]]; then
  if [[ ! -d ffmpeg-$FFV ]]; then
    [[ -f ffmpeg-$FFV.tar.xz ]] || curl -sSL --fail -o ffmpeg-$FFV.tar.xz https://ffmpeg.org/releases/ffmpeg-$FFV.tar.xz
    echo "8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e  ffmpeg-$FFV.tar.xz" | shasum -a 256 -c -
    tar xJf ffmpeg-$FFV.tar.xz
  fi
  # uniquement ce qu'il faut pour sortir le son en WAV : pas de vidéo, pas de réseau, aucun composant GPL
  (cd ffmpeg-$FFV && ./configure --disable-everything --disable-autodetect --enable-zlib --disable-doc --disable-network \
      --disable-ffplay --disable-ffprobe --enable-ffmpeg --enable-small --disable-debug \
      --extra-cflags=-mmacosx-version-min=13.0 --extra-ldflags=-mmacosx-version-min=13.0 \
      --enable-protocol=file,pipe \
      --enable-demuxer=matroska,mov,avi,mpegts,mpegps,flv,asf,ogg,wav,w64,caf,mp3,aac,ac3,eac3,dts,truehd,flac,amr \
      --enable-parser=aac,aac_latm,ac3,dca,mpegaudio,opus,vorbis,flac,mlp \
      --enable-decoder=aac,aac_latm,ac3,eac3,dca,mp3,mp3float,mp2,mp2float,opus,vorbis,flac,alac,truehd,mlp,wmav1,wmav2,wmapro,amrnb,amrwb,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s24be,pcm_s32le,pcm_f32le,pcm_u8,pcm_alaw,pcm_mulaw,pcm_dvd,pcm_bluray,adpcm_ima_wav \
      --enable-encoder=pcm_s16le --enable-muxer=wav --enable-filter=aresample,aformat,anull,pan \
      > ../ffmpeg-configure.log && make -j10 ffmpeg > ../ffmpeg-build.log 2>&1)
fi

echo "▸ Icône"
"$VENV/bin/python" "$SRC/icone.py" icone.png
rm -rf Rature.iconset && mkdir Rature.iconset
for s in 16 32 128 256 512; do
  sips -z $s $s icone.png --out Rature.iconset/icon_${s}x${s}.png > /dev/null
  sips -z $((s * 2)) $((s * 2)) icone.png --out Rature.iconset/icon_${s}x${s}@2x.png > /dev/null
done
iconutil -c icns Rature.iconset -o Rature.icns

echo "▸ Licences open source"
"$VENV/bin/python" "$SRC/licences.py" "$BUILD/whisper.cpp" > /dev/null

echo "▸ Module Cohere (téléchargé à la demande depuis PyPI)"
"$VENV/bin/python" "$SRC/module_cohere.py"

echo "▸ Application"
"$VENV/bin/pyinstaller" --noconfirm --clean --log-level WARN --windowed --name Rature \
  --icon "$BUILD/Rature.icns" --osx-bundle-identifier fr.kirea.rature \
  --add-data "$SRC/web:web" --add-binary "$BUILD/whisper.cpp/build/bin/whisper-cli:bin" \
  --add-binary "$BUILD/lame-3.100/frontend/lame:bin" --add-binary "$BUILD/rnnoise/rnnoise:bin" $( [[ -x "$BUILD/ffmpeg-$FFV/ffmpeg" ]] && echo --add-binary "$BUILD/ffmpeg-$FFV/ffmpeg:bin" ) --hidden-import traitement --hidden-import alignement --hidden-import cohere_worker \
  --add-data "$SRC/module_cohere.json:." \
  --exclude-module mlx --exclude-module mlx_speech --exclude-module tokenizers --exclude-module safetensors \
  --exclude-module soundfile --exclude-module huggingface_hub \
  --exclude-module tkinter --distpath "$BUILD/dist" --workpath "$BUILD/pyi" --specpath "$BUILD" \
  "$SRC/mac.py"

APP="$BUILD/dist/Rature.app"
PL="$APP/Contents/Info.plist"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PL"
plutil -replace CFBundleVersion -string "$VERSION" "$PL"
plutil -replace CFBundleDisplayName -string "Rature" "$PL"
plutil -replace NSHighResolutionCapable -bool true "$PL"
plutil -replace CFBundleDevelopmentRegion -string "en" "$PL"
plutil -replace CFBundleLocalizations -json '["en", "fr"]' "$PL"
plutil -replace LSApplicationCategoryType -string "public.app-category.video" "$PL"
plutil -replace NSHumanReadableCopyright -string "Rature — montage audio par le texte" "$PL"
MINOS=$(find "$APP" -type f \( -name "*.dylib" -o -name "*.so" -o -perm +111 \) -exec otool -l {} \; 2> /dev/null \
  | grep -A4 LC_BUILD_VERSION | awk '/minos/ {print $2}' | sort -V | tail -1)
plutil -replace LSMinimumSystemVersion -string "$MINOS" "$PL"
if [[ -n "${RATURE_IDENTITE:-}" ]]; then
  echo "  signature Developer ID : $RATURE_IDENTITE"
  codesign --force --deep --options runtime --timestamp --entitlements "$SRC/droits.plist" --sign "$RATURE_IDENTITE" "$APP"
else
  codesign --force --deep --sign - "$APP" 2> /dev/null      # signature locale (non reconnue par Gatekeeper)
fi
echo "  $(du -sh "$APP" | cut -f1) · macOS $MINOS minimum · $APP"

echo "▸ Image disque et fichier de version"
PUB="$SRC/../publication/$VERSION"
DMG="$PUB/Rature-$VERSION.dmg"
mkdir -p "$PUB"
rm -rf "$BUILD/dmg" && mkdir -p "$BUILD/dmg"
ditto "$APP" "$BUILD/dmg/Rature.app"
ln -s /Applications "$BUILD/dmg/Applications"
rm -f "$DMG"
hdiutil create -quiet -volname "Rature $VERSION" -srcfolder "$BUILD/dmg" -fs HFS+ -format UDZO -ov "$DMG"
if [[ -n "${RATURE_IDENTITE:-}" ]]; then
  codesign --force --timestamp --sign "$RATURE_IDENTITE" "$DMG"
  if [[ -n "${RATURE_NOTAIRE:-}" ]]; then
    echo "  notarisation par Apple (quelques minutes)…"
    xcrun notarytool submit "$DMG" --keychain-profile "$RATURE_NOTAIRE" --wait
    xcrun stapler staple "$DMG"
  fi
fi
URL_DMG="${RATURE_URL_DMG:-https://github.com/kirea-sas/rature/releases/download/v$VERSION/}Rature-$VERSION.dmg"
"$VENV/bin/python" - "$PUB/version.json" "$VERSION" "$URL_DMG" "$MINOS" "$(stat -f %z "$DMG")" "$SRC/notes_version.json" <<'PY'
import json, sys, datetime
sortie, version, url, minos, taille, notes = sys.argv[1:]
json.dump({'version': version, 'date': datetime.date.today().isoformat(), 'url': url,
           'page': 'https://www.kirea.fr/rature/', 'taille': int(taille), 'macos_min': minos,
           'notes': json.load(open(notes))}, open(sortie, 'w'), ensure_ascii=False, indent=1)
PY
echo "  $(du -h "$DMG" | cut -f1) · $DMG"
echo "  $PUB/version.json"

if [[ "${1:-}" != "--sans-installer" ]]; then
  echo "▸ Installation dans /Applications"
  osascript -e 'tell application "Rature" to quit' 2> /dev/null || true
  rm -rf /Applications/Rature.app
  ditto "$APP" /Applications/Rature.app
  echo "  /Applications/Rature.app"
fi
