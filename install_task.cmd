@echo off
REM Cria tarefa agendada GeoFinOpsSync sem precisar de admin
REM Roda diariamente as 23:50 local
schtasks /Create /TN "GeoFinOpsSync" /TR "python -m geo_finops.sync" /SC DAILY /ST 23:50 /F
schtasks /Query /TN "GeoFinOpsSync"
