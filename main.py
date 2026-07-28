import os
import re
import asyncio
from collections.abc import Iterable

import discord
from discord.ext import commands


TOKEN = os.getenv("DISCORD_TOKEN")
SUPPORT_CREATE_CHANNEL_ID = int(os.getenv("SUPPORT_CREATE_CHANNEL_ID", "0"))
SUPPORT_CATEGORY_ID = int(os.getenv("SUPPORT_CATEGORY_ID", "0"))
SUPPORT_CHANNEL_PREFIX = os.getenv("SUPPORT_CHANNEL_PREFIX", "support").strip() or "support"
SUPPORT_USER_LIMIT = int(os.getenv("SUPPORT_USER_LIMIT", "0"))
SUPPORT_DELETE_DELAY = int(os.getenv("SUPPORT_DELETE_DELAY", "1"))

# Delay used after editing overwrites so Discord has time to propagate them
# before the bot moves the member into the freshly created channel.
SUPPORT_PERMISSION_DELAY = float(os.getenv("SUPPORT_PERMISSION_DELAY", "0.8"))

DEFAULT_LIMITED_ROLE_IDS = [1505912122926694550]
DEFAULT_STAFF_ROLE_IDS = [
    1516635039520260186,
    1505906085901504522,
    1519377368354132110,
    1505905849774641243,
]

VOICE_PERMISSION_FLAGS = (
    "view_channel",
    "connect",
    "speak",
    "use_soundboard",
    "use_voice_activation",
    "priority_speaker",
    "stream",
)


def parse_role_ids(value: str) -> list[int]:
    role_ids: list[int] = []

    for chunk in re.split(r"[,\s;|/-]+", value):
        chunk = chunk.strip()
        if chunk.isdigit():
            role_ids.append(int(chunk))

    return role_ids


def unique_role_ids(role_ids: Iterable[int]) -> list[int]:
    unique_ids: list[int] = []
    seen: set[int] = set()

    for role_id in role_ids:
        if role_id not in seen:
            unique_ids.append(role_id)
            seen.add(role_id)

    return unique_ids


def configured_role_ids(env_names: Iterable[str], default_role_ids: Iterable[int]) -> list[int]:
    role_ids = list(default_role_ids)

    for env_name in env_names:
        role_ids.extend(parse_role_ids(os.getenv(env_name, "")))

    return unique_role_ids(role_ids)


SUPPORT_LIMITED_ROLE_IDS = configured_role_ids(
    ("SUPPORT_LIMITED_ROLE_IDS", "SUPPORT_VIEW_ROLE_IDS"),
    DEFAULT_LIMITED_ROLE_IDS,
)
SUPPORT_STAFF_ROLE_IDS = configured_role_ids(
    ("SUPPORT_STAFF_ROLE_IDS", "SUPPORT_CONNECT_ROLE_IDS"),
    DEFAULT_STAFF_ROLE_IDS,
)

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
dynamic_support_channels: set[int] = set()
support_creation_locks: dict[int, asyncio.Lock] = {}


def unsupported_permission_flags() -> list[str]:
    valid_flags = getattr(discord.Permissions, "VALID_FLAGS", None)
    if valid_flags is None:
        return []

    return [flag for flag in VOICE_PERMISSION_FLAGS if flag not in valid_flags]


def permission_overwrite(**permissions: bool | None) -> discord.PermissionOverwrite:
    valid_flags = getattr(discord.Permissions, "VALID_FLAGS", None)
    if valid_flags is not None:
        permissions = {
            name: value
            for name, value in permissions.items()
            if name in valid_flags
        }

    return discord.PermissionOverwrite(**permissions)


def hidden_everyone_permissions() -> discord.PermissionOverwrite:
    """@everyone must not see or use support channels."""
    return permission_overwrite(
        view_channel=False,
        connect=False,
        speak=False,
        use_soundboard=False,
        use_voice_activation=False,
        priority_speaker=False,
        stream=False,
    )


def limited_voice_permissions() -> discord.PermissionOverwrite:
    """Permissions requested for role 1505912122926694550."""
    return permission_overwrite(
        view_channel=True,
        connect=False,
        speak=True,
        use_soundboard=False,
        use_voice_activation=True,
        priority_speaker=True,
        stream=True,
    )


def staff_voice_permissions() -> discord.PermissionOverwrite:
    """Full voice permissions for the configured staff roles."""
    return permission_overwrite(
        view_channel=True,
        connect=True,
        speak=True,
        use_soundboard=True,
        use_voice_activation=True,
        priority_speaker=True,
        stream=True,
    )


def bot_voice_permissions() -> discord.PermissionOverwrite:
    return permission_overwrite(
        view_channel=True,
        connect=True,
        speak=True,
        use_soundboard=True,
        use_voice_activation=True,
        priority_speaker=True,
        stream=True,
        manage_channels=True,
        move_members=True,
    )


def has_any_role(member: discord.Member, role_ids: list[int]) -> bool:
    return any(role.id in role_ids for role in member.roles)


def can_create_or_enter_support(member: discord.Member) -> bool:
    return has_any_role(member, SUPPORT_STAFF_ROLE_IDS)


def get_creation_lock(guild_id: int) -> asyncio.Lock:
    lock = support_creation_locks.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        support_creation_locks[guild_id] = lock
    return lock


def get_support_number(channel_name: str) -> int | None:
    cleaned_name = channel_name.strip()
    patterns = [
        rf"^{re.escape(SUPPORT_CHANNEL_PREFIX)}[\s#_-]+(\d+)$",
        r"^support[\s#_-]+(\d+)$",
    ]

    for pattern in patterns:
        match = re.match(pattern, cleaned_name, re.IGNORECASE)
        if match:
            return int(match.group(1))

    return None


def is_dynamic_support_channel(channel: discord.VoiceChannel | None) -> bool:
    if channel is None:
        return False
    if channel.id in dynamic_support_channels:
        return True
    return get_support_number(channel.name) is not None


def get_next_support_number(
    guild: discord.Guild,
    category: discord.CategoryChannel | None,
) -> int:
    used_numbers = set()

    for channel in guild.voice_channels:
        if category and channel.category_id != category.id:
            continue

        number = get_support_number(channel.name)
        if number is not None:
            used_numbers.add(number)

    number = 1
    while number in used_numbers:
        number += 1

    return number


def build_support_overwrites(
    guild: discord.Guild,
    creator: discord.Member | None = None,
) -> dict:
    overwrites: dict = {
        guild.default_role: hidden_everyone_permissions(),
    }

    bot_member = guild.me
    if bot_member:
        overwrites[bot_member] = bot_voice_permissions()

    for role_id in SUPPORT_LIMITED_ROLE_IDS:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = limited_voice_permissions()
        else:
            print(f"ATTENZIONE: ruolo limited non trovato: {role_id}")

    found_staff_role = False
    for role_id in SUPPORT_STAFF_ROLE_IDS:
        role = guild.get_role(role_id)
        if role:
            found_staff_role = True
            overwrites[role] = staff_voice_permissions()
        else:
            print(f"ATTENZIONE: ruolo staff non trovato: {role_id}")

    if creator is not None:
        overwrites[creator] = staff_voice_permissions()

    if not found_staff_role:
        print("ATTENZIONE: nessun ruolo staff trovato. Controlla SUPPORT_STAFF_ROLE_IDS.")

    return overwrites


async def apply_support_permissions(
    channel: discord.VoiceChannel,
    creator: discord.Member | None = None,
    reason: str = "Aggiornamento permessi support",
) -> discord.VoiceChannel:
    overwrites = build_support_overwrites(channel.guild, creator)

    await channel.edit(overwrites=overwrites, reason=reason)
    await asyncio.sleep(max(SUPPORT_PERMISSION_DELAY, 0.0))

    refreshed_channel = channel.guild.get_channel(channel.id)
    if not isinstance(refreshed_channel, discord.VoiceChannel):
        fetched_channel = await channel.guild.fetch_channel(channel.id)
        if not isinstance(fetched_channel, discord.VoiceChannel):
            raise RuntimeError("Il canale support creato non esiste piu.")
        refreshed_channel = fetched_channel

    await refreshed_channel.edit(overwrites=overwrites, reason=reason)
    return refreshed_channel


async def delete_if_empty(channel_id: int):
    await asyncio.sleep(SUPPORT_DELETE_DELAY)
    channel = bot.get_channel(channel_id)

    if not isinstance(channel, discord.VoiceChannel):
        dynamic_support_channels.discard(channel_id)
        return

    non_bot_members = [member for member in channel.members if not member.bot]
    if len(non_bot_members) == 0 and is_dynamic_support_channel(channel):
        try:
            await channel.delete(reason="Canale support vuoto")
            dynamic_support_channels.discard(channel_id)
            print(f"Canale eliminato: {channel.name}")
        except Exception as e:
            print(f"Errore eliminando il canale: {e}")


@bot.event
async def on_ready():
    print(f"Bot Support online come {bot.user} | Server: {len(bot.guilds)}")
    print(f"SUPPORT_LIMITED_ROLE_IDS={SUPPORT_LIMITED_ROLE_IDS}")
    print(f"SUPPORT_STAFF_ROLE_IDS={SUPPORT_STAFF_ROLE_IDS}")
    print("VERSIONE: SUPPORT_VOICE_PERMISSIONS_IT_V1")

    missing_flags = unsupported_permission_flags()
    if missing_flags:
        print(
            "ATTENZIONE: questa versione di discord.py non supporta questi permessi: "
            f"{', '.join(missing_flags)}. Aggiorna discord.py."
        )

    for guild in bot.guilds:
        for channel in guild.voice_channels:
            if is_dynamic_support_channel(channel):
                dynamic_support_channels.add(channel.id)

                try:
                    await apply_support_permissions(
                        channel,
                        reason="Fix permessi canali support esistenti",
                    )
                    print(f"Permessi aggiornati per: {channel.name}")
                except Exception as e:
                    print(f"Non posso aggiornare i permessi per {channel.name}: {e}")

                if len([m for m in channel.members if not m.bot]) == 0:
                    asyncio.create_task(delete_if_empty(channel.id))

    try:
        await bot.tree.sync()
        print("Comandi slash sincronizzati.")
    except Exception as e:
        print(f"Errore sincronizzando i comandi slash: {e}")


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    if member.bot:
        return

    if after.channel and after.channel.id == SUPPORT_CREATE_CHANNEL_ID:
        if not can_create_or_enter_support(member):
            try:
                await member.move_to(None, reason="Manca ruolo staff per creare support")
            except Exception:
                pass
            return

        guild = member.guild
        create_channel = after.channel

        category = None
        if SUPPORT_CATEGORY_ID:
            found = guild.get_channel(SUPPORT_CATEGORY_ID)
            if isinstance(found, discord.CategoryChannel):
                category = found

        if category is None:
            category = create_channel.category

        async with get_creation_lock(guild.id):
            number = get_next_support_number(guild, category)
            channel_name = f"{SUPPORT_CHANNEL_PREFIX} {number}"
            new_channel: discord.VoiceChannel | None = None

            try:
                initial_overwrites = build_support_overwrites(guild, creator=member)

                new_channel = await guild.create_voice_channel(
                    name=channel_name,
                    category=category,
                    user_limit=SUPPORT_USER_LIMIT,
                    overwrites=initial_overwrites,
                    reason=f"Support creato da {member}",
                )
                dynamic_support_channels.add(new_channel.id)

                new_channel = await apply_support_permissions(
                    new_channel,
                    creator=member,
                    reason="Fix permessi alla creazione del support",
                )

                if (
                    member.voice
                    and member.voice.channel
                    and member.voice.channel.id == SUPPORT_CREATE_CHANNEL_ID
                ):
                    await member.move_to(
                        new_channel,
                        reason="Spostato nel canale support creato",
                    )
                    await asyncio.sleep(1)

                refreshed = guild.get_channel(new_channel.id)
                if isinstance(refreshed, discord.VoiceChannel):
                    non_bot_members = [m for m in refreshed.members if not m.bot]
                    if not non_bot_members:
                        print(f"Canale support rimasto vuoto dopo creazione: {refreshed.name}")
                        asyncio.create_task(delete_if_empty(refreshed.id))
                    else:
                        print(f"Canale creato: {refreshed.name} per {member}")

            except Exception as e:
                print(f"Errore creando il canale support: {type(e).__name__}: {e}")

                if isinstance(new_channel, discord.VoiceChannel):
                    try:
                        current_channel = guild.get_channel(new_channel.id)
                        if isinstance(current_channel, discord.VoiceChannel):
                            non_bot_members = [
                                member for member in current_channel.members if not member.bot
                            ]
                            if not non_bot_members:
                                await current_channel.delete(
                                    reason="Canale support orfano dopo errore"
                                )
                                dynamic_support_channels.discard(current_channel.id)
                                print(f"Canale orfano eliminato: {current_channel.name}")
                    except Exception as delete_error:
                        print(
                            "Non posso eliminare il canale support orfano: "
                            f"{type(delete_error).__name__}: {delete_error}"
                        )

                try:
                    await member.move_to(None)
                except Exception:
                    pass

    if before.channel and is_dynamic_support_channel(before.channel):
        asyncio.create_task(delete_if_empty(before.channel.id))


@bot.tree.command(
    name="suport_status",
    description="Mostra quanti canali support sono attivi.",
)
async def suport_status(interaction: discord.Interaction):
    if (
        not isinstance(interaction.user, discord.Member)
        or not can_create_or_enter_support(interaction.user)
    ):
        await interaction.response.send_message(
            "Non hai il permesso di usare questo comando.",
            ephemeral=True,
        )
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "Questo comando puo essere usato solo nel server.",
            ephemeral=True,
        )
        return

    active_channels = [
        channel
        for channel in interaction.guild.voice_channels
        if is_dynamic_support_channel(channel)
    ]
    await interaction.response.send_message(
        f"Canali support attivi: `{len(active_channels)}`",
        ephemeral=True,
    )


@bot.tree.command(
    name="suport_cleanup",
    description="Elimina i canali support vuoti.",
)
async def suport_cleanup(interaction: discord.Interaction):
    if (
        not isinstance(interaction.user, discord.Member)
        or not can_create_or_enter_support(interaction.user)
    ):
        await interaction.response.send_message(
            "Non hai il permesso di usare questo comando.",
            ephemeral=True,
        )
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "Questo comando puo essere usato solo nel server.",
            ephemeral=True,
        )
        return

    deleted = 0
    for channel in list(interaction.guild.voice_channels):
        if is_dynamic_support_channel(channel):
            non_bot_members = [member for member in channel.members if not member.bot]
            if len(non_bot_members) == 0:
                try:
                    await channel.delete(reason="Pulizia canali support vuoti")
                    dynamic_support_channels.discard(channel.id)
                    deleted += 1
                except Exception as e:
                    print(f"Non posso eliminare {channel.name}: {e}")

    await interaction.response.send_message(
        f"Pulizia completata. Canali eliminati: `{deleted}`",
        ephemeral=True,
    )


@bot.tree.command(
    name="suport_fix_permissions",
    description="Ripara i permessi dei canali support.",
)
async def suport_fix_permissions(interaction: discord.Interaction):
    if (
        not isinstance(interaction.user, discord.Member)
        or not can_create_or_enter_support(interaction.user)
    ):
        await interaction.response.send_message(
            "Non hai il permesso di usare questo comando.",
            ephemeral=True,
        )
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "Questo comando puo essere usato solo nel server.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    fixed = 0
    failed = 0

    for channel in interaction.guild.voice_channels:
        if is_dynamic_support_channel(channel):
            try:
                await apply_support_permissions(
                    channel,
                    reason="Comando support_fix_permissions",
                )
                fixed += 1
            except Exception as e:
                failed += 1
                print(f"Non posso riparare {channel.name}: {e}")

    await interaction.followup.send(
        f"Permessi aggiornati: `{fixed}`\nErrori: `{failed}`",
        ephemeral=True,
    )


if not TOKEN:
    raise RuntimeError("Manca DISCORD_TOKEN nelle variabili d'ambiente.")
if SUPPORT_CREATE_CHANNEL_ID == 0:
    raise RuntimeError("Manca SUPPORT_CREATE_CHANNEL_ID nelle variabili d'ambiente.")
if not SUPPORT_STAFF_ROLE_IDS:
    raise RuntimeError("Manca SUPPORT_STAFF_ROLE_IDS nelle variabili d'ambiente.")

bot.run(TOKEN)
