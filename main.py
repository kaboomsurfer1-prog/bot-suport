import os
import re
import asyncio
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
SUPPORT_CREATE_CHANNEL_ID = int(os.getenv("SUPPORT_CREATE_CHANNEL_ID", "0"))

SUPPORT_VIEW_ROLE_IDS = [
    int(role_id.strip())
    for role_id in os.getenv("SUPPORT_VIEW_ROLE_IDS", "1505912122926694550").split(",")
    if role_id.strip().isdigit()
]

SUPPORT_CONNECT_ROLE_IDS = [
    int(role_id.strip())
    for role_id in os.getenv("SUPPORT_CONNECT_ROLE_IDS", "1516635039520260186").split(",")
    if role_id.strip().isdigit()
]

SUPPORT_CATEGORY_ID = int(os.getenv("SUPPORT_CATEGORY_ID", "0"))
SUPPORT_CHANNEL_PREFIX = os.getenv("SUPPORT_CHANNEL_PREFIX", "‧🔊 ꜱᴜᴩᴩᴏʀᴛ")
SUPPORT_USER_LIMIT = int(os.getenv("SUPPORT_USER_LIMIT", "0"))
SUPPORT_DELETE_DELAY = int(os.getenv("SUPPORT_DELETE_DELAY", "1"))

# Piccolo ritardo per permettere a Discord di registrare completamente
# il nuovo canale prima di spostare l'utente.
SUPPORT_PERMISSION_DELAY = float(os.getenv("SUPPORT_PERMISSION_DELAY", "0.8"))

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
dynamic_support_channels: set[int] = set()
support_creation_locks: dict[int, asyncio.Lock] = {}


def has_any_role(member: discord.Member, role_ids: list[int]) -> bool:
    return any(role.id in role_ids for role in member.roles)


def can_create_or_enter_support(member: discord.Member) -> bool:
    return has_any_role(member, SUPPORT_CONNECT_ROLE_IDS)


def get_creation_lock(guild_id: int) -> asyncio.Lock:
    lock = support_creation_locks.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        support_creation_locks[guild_id] = lock
    return lock


def get_support_number(channel_name: str) -> int | None:
    pattern = rf"^{re.escape(SUPPORT_CHANNEL_PREFIX.strip())}\s+(\d+)$"
    match = re.match(pattern, channel_name.strip(), re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


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


def full_voice_permissions() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        connect=True,
        speak=True,
        stream=True,
        use_voice_activation=True,
    )


def build_support_overwrites(
    guild: discord.Guild,
    creator: discord.Member | None = None,
) -> dict:
    """
    @everyone non vede e non entra.

    SUPPORT_VIEW_ROLE_IDS:
    può vedere il canale, ma non può entrare.

    SUPPORT_CONNECT_ROLE_IDS:
    può vedere, entrare, parlare, condividere lo schermo
    e usare l'attività vocale.

    Il creatore riceve anche un permesso diretto temporaneo/stabile,
    così Discord non lo sposta nel primo canale con permessi incompleti.
    """
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            connect=False,
            speak=False,
            stream=False,
            use_voice_activation=False,
        )
    }

    bot_member = guild.me
    if bot_member:
        overwrites[bot_member] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            manage_channels=True,
            move_members=True,
        )

    for role_id in SUPPORT_VIEW_ROLE_IDS:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                connect=False,
                speak=False,
                stream=False,
                use_voice_activation=False,
            )
        else:
            print(f"ATENTIE: rol view-only negasit: {role_id}")

    found_connect_role = False
    for role_id in SUPPORT_CONNECT_ROLE_IDS:
        role = guild.get_role(role_id)
        if role:
            found_connect_role = True
            overwrites[role] = full_voice_permissions()
        else:
            print(f"ATENTIE: rol connect negasit: {role_id}")

    if creator is not None:
        overwrites[creator] = full_voice_permissions()

    if not found_connect_role:
        print("ATENTIE: niciun rol connect gasit. Verifica SUPPORT_CONNECT_ROLE_IDS.")

    return overwrites


async def apply_support_permissions(
    channel: discord.VoiceChannel,
    creator: discord.Member | None = None,
    reason: str = "Actualizare permisiuni suport",
) -> discord.VoiceChannel:
    """Applica i permessi, aspetta la propagazione e li applica di nuovo."""
    overwrites = build_support_overwrites(channel.guild, creator)

    await channel.edit(overwrites=overwrites, reason=reason)
    await asyncio.sleep(max(SUPPORT_PERMISSION_DELAY, 0.0))

    # Recupera nuovamente il canale per evitare di usare una copia cache
    # non ancora aggiornata, soprattutto sul primo canale creato.
    refreshed_channel = channel.guild.get_channel(channel.id)
    if not isinstance(refreshed_channel, discord.VoiceChannel):
        fetched_channel = await channel.guild.fetch_channel(channel.id)
        if not isinstance(fetched_channel, discord.VoiceChannel):
            raise RuntimeError("Canalul suport creat nu mai exista.")
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
            await channel.delete(reason="Canal suport gol")
            dynamic_support_channels.discard(channel_id)
            print(f"Canal sters: {channel.name}")
        except Exception as e:
            print(f"Eroare la stergerea canalului: {e}")


@bot.event
async def on_ready():
    print(f"Bot Suport online ca {bot.user} | Servere: {len(bot.guilds)}")
    print(f"SUPPORT_VIEW_ROLE_IDS={SUPPORT_VIEW_ROLE_IDS}")
    print(f"SUPPORT_CONNECT_ROLE_IDS={SUPPORT_CONNECT_ROLE_IDS}")
    print("VERSIUNE: SUPPORT_ORPHAN_CHANNEL_CLEANUP_V2")

    for guild in bot.guilds:
        for channel in guild.voice_channels:
            if is_dynamic_support_channel(channel):
                dynamic_support_channels.add(channel.id)

                try:
                    await apply_support_permissions(
                        channel,
                        reason="SUPPORT_FIRST_CHANNEL_PERMISSION_FIX",
                    )
                    print(f"Permisiuni actualizate pentru: {channel.name}")
                except Exception as e:
                    print(f"Nu pot actualiza permisiunile pentru {channel.name}: {e}")

                if len([m for m in channel.members if not m.bot]) == 0:
                    asyncio.create_task(delete_if_empty(channel.id))

    try:
        await bot.tree.sync()
        print("Comenzile slash au fost sincronizate.")
    except Exception as e:
        print(f"Eroare la sincronizarea comenzilor slash: {e}")


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
                await member.move_to(None, reason="Fara rol connect pentru Creare Suport")
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

        # Evita che due utenti creino contemporaneamente due canali
        # con lo stesso numero.
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
                    reason=f"Suport creat de {member}",
                )
                dynamic_support_channels.add(new_channel.id)

                # Riapplica i permessi e aspetta prima di spostare l'utente.
                new_channel = await apply_support_permissions(
                    new_channel,
                    creator=member,
                    reason="Fix speak si voice activity la creare",
                )

                # Se l'utente è ancora in Creare Suport, lo sposta.
                if (
                    member.voice
                    and member.voice.channel
                    and member.voice.channel.id == SUPPORT_CREATE_CHANNEL_ID
                ):
                    await member.move_to(
                        new_channel,
                        reason="Mutat in canalul suport creat",
                    )

                    # Attende che Discord aggiorni lo stato vocale.
                    await asyncio.sleep(1)

                # Se nessuno è entrato nel nuovo canale, lo elimina automaticamente.
                refreshed = guild.get_channel(new_channel.id)
                if isinstance(refreshed, discord.VoiceChannel):
                    non_bot_members = [m for m in refreshed.members if not m.bot]
                    if not non_bot_members:
                        print(f"Canal suport ramas gol dupa creare: {refreshed.name}")
                        asyncio.create_task(delete_if_empty(refreshed.id))
                    else:
                        print(f"Canal creat: {refreshed.name} pentru {member}")

            except Exception as e:
                print(f"Eroare la crearea canalului suport: {type(e).__name__}: {e}")

                # Non lascia canali orfani se la creazione riesce ma lo spostamento fallisce.
                if isinstance(new_channel, discord.VoiceChannel):
                    try:
                        current_channel = guild.get_channel(new_channel.id)
                        if isinstance(current_channel, discord.VoiceChannel):
                            non_bot_members = [m for m in current_channel.members if not m.bot]
                            if not non_bot_members:
                                await current_channel.delete(
                                    reason="Canal suport orfan dupa eroare la mutare"
                                )
                                dynamic_support_channels.discard(current_channel.id)
                                print(f"Canal orfan sters: {current_channel.name}")
                    except Exception as delete_error:
                        print(
                            f"Nu pot sterge canalul suport orfan: "
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
    description="Arata cate canale suport sunt active.",
)
async def suport_status(interaction: discord.Interaction):
    if (
        not isinstance(interaction.user, discord.Member)
        or not can_create_or_enter_support(interaction.user)
    ):
        await interaction.response.send_message(
            "❌ Nu ai permisiunea sa folosesti aceasta comanda.",
            ephemeral=True,
        )
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Comanda poate fi folosita doar pe server.",
            ephemeral=True,
        )
        return

    active_channels = [
        channel
        for channel in interaction.guild.voice_channels
        if is_dynamic_support_channel(channel)
    ]
    await interaction.response.send_message(
        f"✅ Canale suport active: `{len(active_channels)}`",
        ephemeral=True,
    )


@bot.tree.command(
    name="suport_cleanup",
    description="Sterge canalele suport goale.",
)
async def suport_cleanup(interaction: discord.Interaction):
    if (
        not isinstance(interaction.user, discord.Member)
        or not can_create_or_enter_support(interaction.user)
    ):
        await interaction.response.send_message(
            "❌ Nu ai permisiunea sa folosesti aceasta comanda.",
            ephemeral=True,
        )
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Comanda poate fi folosita doar pe server.",
            ephemeral=True,
        )
        return

    deleted = 0
    for channel in list(interaction.guild.voice_channels):
        if is_dynamic_support_channel(channel):
            non_bot_members = [member for member in channel.members if not member.bot]
            if len(non_bot_members) == 0:
                try:
                    await channel.delete(reason="Curatare canale suport goale")
                    dynamic_support_channels.discard(channel.id)
                    deleted += 1
                except Exception as e:
                    print(f"Nu pot sterge {channel.name}: {e}")

    await interaction.response.send_message(
        f"✅ Curatare finalizata. Canale sterse: `{deleted}`",
        ephemeral=True,
    )


@bot.tree.command(
    name="suport_fix_permissions",
    description="Repara permisiunile canalelor support.",
)
async def suport_fix_permissions(interaction: discord.Interaction):
    if (
        not isinstance(interaction.user, discord.Member)
        or not can_create_or_enter_support(interaction.user)
    ):
        await interaction.response.send_message(
            "❌ Nu ai permisiunea sa folosesti aceasta comanda.",
            ephemeral=True,
        )
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Comanda poate fi folosita doar pe server.",
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
                    reason="Comanda suport_fix_permissions",
                )
                fixed += 1
            except Exception as e:
                failed += 1
                print(f"Nu pot repara {channel.name}: {e}")

    await interaction.followup.send(
        f"✅ Permisiuni actualizate: `{fixed}`\n❌ Erori: `{failed}`",
        ephemeral=True,
    )


if not TOKEN:
    raise RuntimeError("Lipseste DISCORD_TOKEN in variabilele Railway.")
if SUPPORT_CREATE_CHANNEL_ID == 0:
    raise RuntimeError("Lipseste SUPPORT_CREATE_CHANNEL_ID in variabilele Railway.")
if not SUPPORT_CONNECT_ROLE_IDS:
    raise RuntimeError("Lipseste SUPPORT_CONNECT_ROLE_IDS in variabilele Railway.")

bot.run(TOKEN)
