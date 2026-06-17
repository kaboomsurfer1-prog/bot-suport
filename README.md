# Bot Suport Discord

Bot per creare canali vocali support dinamici.

## Funzionamento

- Lo staff entra/clicca nel canale vocale `Creare Suport`.
- Il bot crea automaticamente:
  - `‧🔊 ꜱᴜᴩᴩᴏʀᴛ 1`
  - `‧🔊 ꜱᴜᴩᴩᴏʀᴛ 2`
  - `‧🔊 ꜱᴜᴩᴩᴏʀᴛ 3`
- Quando il canale resta vuoto, il bot lo cancella.

## Variabili Railway

DISCORD_TOKEN=token_bot
SUPPORT_CREATE_CHANNEL_ID=id_canale_vocale_Creare_Suport
SUPPORT_STAFF_ROLE_IDS=1516635039520260186
SUPPORT_CATEGORY_ID=0
SUPPORT_CHANNEL_PREFIX=‧🔊 ꜱᴜᴩᴩᴏʀᴛ
SUPPORT_USER_LIMIT=0
SUPPORT_DELETE_DELAY=1

## Permessi bot richiesti

Il bot deve avere:
- View Channels
- Connect
- Move Members
- Manage Channels

## Developer Portal

Nel Bot abilita:
- Server Members Intent

## Comandi

/suport_status
/suport_cleanup
