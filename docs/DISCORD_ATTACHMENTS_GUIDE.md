# Discord Attachment Recovery Guide — CYOA Downloader v1.0.8

Discord attachment recovery is part of the normal CYOA Downloader pipeline.
There is no separate recovery application, input JSON selector, or output JSON
workflow.

## How it works

During a normal download, the application obtains project data by reading
`project.json` or extracting an embedded project object from a JavaScript
bundle. The image pipeline then checks fields such as `image`,
`backgroundImage`, and `rowBackgroundImage` for supported Discord attachment
URLs:

```text
https://cdn.discordapp.com/attachments/CHANNEL_ID/ATTACHMENT_ID/file.png?...
https://media.discordapp.net/attachments/CHANNEL_ID/ATTACHMENT_ID/file.png?...
```

The original CDN URL is tried first. If Discord returns `401`, `403`, `404`, or
`410`, a configured bot token can request a refreshed URL through Discord API
v10. The normal image pipeline downloads the refreshed asset and changes the
project reference to a local path.

## Server access requirements

A bot does not need to join the CYOA owner's server when the project already
contains an `/attachments/` CDN URL. The refresh endpoint consumes the
attachment URL itself; the downloader does not read guilds, channels, or
messages.

Server access would be needed only when the sole input is a Discord message
link and software must read the message to discover its attachments. CYOA
Downloader does not implement that workflow.

## Create and save a bot token

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create or select an application.
3. Open **Bot** and create the bot if necessary.
4. Select **Reset Token** or **View Token**, then copy the value.
5. Never place the token in project files or share it with another person.

Privileged gateway intents and an OAuth2 invitation to the source server are
not required for URL refresh.

In the GUI, open **Settings / Maintenance**, locate **Discord Bot Token**, save
the token, and select **Test**. The token is stored directly in:

```text
C:\Users\<name>\.cyoa_downloader\settings.json
```

The relevant JSON field is:

```json
"discord_bot_token": "YOUR_DISCORD_BOT_TOKEN"
```

There is no Discord storage-mode selector or separate enable checkbox. Normal
downloads automatically use the saved value when refresh is necessary.

## CLI

Provide a token for one process:

```powershell
python cyoa_downloader.py "https://example.com/cyoa/" `
  --discord-token "BOT_TOKEN"
```

You can also set `DISCORD_BOT_TOKEN`. Use `--no-discord-refresh` to disable
refresh for one run.

## Troubleshooting

- Confirm that the URL uses a supported host and contains `/attachments/`.
- Test the token again after any reset; an old token becomes invalid.
- Confirm that the machine can reach `discord.com` and the Discord CDN.
- Inspect the download log for `Discord URL refresh failed`.
- A bot token cannot recover an attachment that was permanently deleted from
  Discord.

The token is excluded from download output and redacted from logs, but it is
plain text in the active settings file. Do not publish that file. If the token
is exposed, reset it in the Developer Portal and save the new value.
