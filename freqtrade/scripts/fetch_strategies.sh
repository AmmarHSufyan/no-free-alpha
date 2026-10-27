#!/usr/bin/env bash
# Fetch the community strategies used in the benchmark at the tested commit. They are GPL-3.0 licensed, so they
# are downloaded from their own repository instead of being redistributed here.
set -eu
cd "$(dirname "$0")/.."
[ -d vendor/freqtrade-strategies ] || git clone https://github.com/freqtrade/freqtrade-strategies vendor/freqtrade-strategies
git -C vendor/freqtrade-strategies checkout -q f3340ce
src=vendor/freqtrade-strategies/user_data/strategies
mkdir -p user_data/strategies
cp "$src"/Strategy00{1,2,3,4,5}.py "$src"/Supertrend.py user_data/strategies/
cp "$src"/berlinguyinca/{BbandRsi,CombinedBinHAndCluc,ClucMay72018,ADXMomentum}.py user_data/strategies/
ls user_data/strategies
