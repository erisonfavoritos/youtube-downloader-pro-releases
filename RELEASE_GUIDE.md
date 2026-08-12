# Publicação e licenças

O repositório público contém somente executáveis e `update.json`.

## Publicar uma versão

1. Atualize `VERSION` em `youtube_downloader.py`.
2. Execute `build_exe.ps1`.
3. Calcule o SHA-256 do novo executável.
4. Crie uma Release no GitHub com a tag `vX.Y.Z`.
5. Anexe `YouTubeDownloaderPRO.exe`.
6. Atualize o canal desejado em `update.json` por último.

Publicar o manifesto por último impede que clientes encontrem uma atualização cujo arquivo ainda não está disponível.

## Licença permanente

Peça ao usuário o ID mostrado na tela de login e adicione-o em `licenses.permanent` no `update.json`.

## Bloqueio remoto

Mova ou adicione o ID em `licenses.blocked`. A lista de bloqueio tem prioridade sobre a licença permanente.
