@echo off
cd /d "C:\Users\PCMARCELLO\Documents\Projeitos_Claude\Projeitos-scraping\scraping_statarea"

echo [1/2] Baixando backtest do dia anterior...
python scraper.py backtest 1

echo [2/2] Raspando jogos de hoje...
python scraper.py full

echo Concluido!
