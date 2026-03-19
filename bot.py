"""
NFT Auto-Mint Telegram Bot — Base Chain
=======================================
Plain English: This file is the brain of your bot.
It listens to your Telegram messages and triggers mints.
"""

import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from minter import NFTMinter
from dotenv import load_dotenv

load_dotenv()

# --- LOGGING (shows activity in your terminal/server logs) ---
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# --- LOAD YOUR SECRETS FROM .env FILE ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_TELEGRAM_USER_ID"))  # Only YOU can use this bot

# --- BOOT UP THE MINTER ---
minter = NFTMinter()


# ============================================================
# SECURITY CHECK — runs before every command
# Plain English: If someone else finds your bot, they can't use it
# ============================================================
def only_me(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ALLOWED_USER_ID:
            await update.message.reply_text("🚫 Unauthorized.")
            return
        return await func(update, context)
    return wrapper


# ============================================================
# /start — Welcome screen
# ============================================================
@only_me
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💎 Mint NFT", callback_data="mint_menu")],
        [InlineKeyboardButton("💰 Wallet Balance", callback_data="balance")],
        [InlineKeyboardButton("⛽ Gas Price", callback_data="gas")],
        [InlineKeyboardButton("📋 Active Jobs", callback_data="jobs")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🤖 *NFT Mint Bot — Base Chain*\n\n"
        "What do you want to do?\n\n"
        "_Tip: Use /mint <contract\\_address> to quick-mint_",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


# ============================================================
# /mint <contract_address> [quantity] [max_price_eth]
# Plain English: This is the main command. You paste the NFT
# contract address and the bot mints for you.
#
# Example: /mint 0xAbC123... 1 0.01
# ============================================================
@only_me
async def mint_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args

    if not args:
        await update.message.reply_text(
            "❌ *Usage:*\n`/mint <contract_address> [quantity] [max_price_eth]`\n\n"
            "*Example:*\n`/mint 0xAbc123... 1 0.005`",
            parse_mode="Markdown"
        )
        return

    contract_address = args[0]
    quantity = int(args[1]) if len(args) > 1 else 1
    max_price_eth = float(args[2]) if len(args) > 2 else 0.01

    # Validate it looks like an address
    if not contract_address.startswith("0x") or len(contract_address) != 42:
        await update.message.reply_text("❌ That doesn't look like a valid contract address.")
        return

    msg = await update.message.reply_text(
        f"⏳ *Preparing to mint...*\n\n"
        f"📄 Contract: `{contract_address[:6]}...{contract_address[-4:]}`\n"
        f"🔢 Quantity: {quantity}\n"
        f"💸 Max price: {max_price_eth} ETH",
        parse_mode="Markdown"
    )

    try:
        result = await minter.mint(
            contract_address=contract_address,
            quantity=quantity,
            max_price_eth=max_price_eth
        )

        if result["success"]:
            await msg.edit_text(
                f"✅ *Mint Successful!*\n\n"
                f"🔗 TX Hash: `{result['tx_hash']}`\n"
                f"⛽ Gas used: {result['gas_used']}\n"
                f"💸 Total cost: {result['total_cost_eth']:.6f} ETH\n\n"
                f"[View on BaseScan](https://basescan.org/tx/{result['tx_hash']})",
                parse_mode="Markdown"
            )
        else:
            await msg.edit_text(
                f"❌ *Mint Failed*\n\n"
                f"Reason: {result['error']}\n\n"
                f"_Check gas price or contract status_",
                parse_mode="Markdown"
            )

    except Exception as e:
        log.error(f"Mint error: {e}")
        await msg.edit_text(f"💥 Unexpected error: `{str(e)}`", parse_mode="Markdown")


# ============================================================
# /watch <contract_address> — Auto-mint when mint goes live
# Plain English: You give it a contract BEFORE the mint opens.
# The bot keeps checking every few seconds and fires the moment
# minting becomes available. Set it and forget it.
# ============================================================
@only_me
async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args

    if not args:
        await update.message.reply_text(
            "❌ *Usage:*\n`/watch <contract_address> [quantity] [max_price_eth]`\n\n"
            "*Example:*\n`/watch 0xAbc123... 1 0.005`\n\n"
            "_The bot will auto-mint the moment the contract opens_",
            parse_mode="Markdown"
        )
        return

    contract_address = args[0]
    quantity = int(args[1]) if len(args) > 1 else 1
    max_price_eth = float(args[2]) if len(args) > 2 else 0.01

    await update.message.reply_text(
        f"👁 *Watching contract...*\n\n"
        f"📄 `{contract_address[:6]}...{contract_address[-4:]}`\n"
        f"🔢 Will mint: {quantity}\n"
        f"💸 Max price: {max_price_eth} ETH\n\n"
        f"_I'll notify you when I fire. Use /jobs to see active watches. /stopwatch to cancel._",
        parse_mode="Markdown"
    )

    # Store the watch job in bot context
    if "watch_jobs" not in context.bot_data:
        context.bot_data["watch_jobs"] = {}

    job = context.job_queue.run_repeating(
        callback=watch_and_mint_job,
        interval=5,  # Check every 5 seconds
        first=1,
        data={
            "contract_address": contract_address,
            "quantity": quantity,
            "max_price_eth": max_price_eth,
            "chat_id": update.effective_chat.id,
        },
        name=contract_address
    )
    context.bot_data["watch_jobs"][contract_address] = job


async def watch_and_mint_job(context: ContextTypes.DEFAULT_TYPE):
    """Plain English: This runs in the background every 5 seconds.
    It checks if the contract is mintable yet. The moment it is, BOOM."""
    data = context.job.data
    contract_address = data["contract_address"]

    is_live = await minter.is_mint_live(contract_address)

    if is_live:
        # Stop watching — we're going in
        context.job.schedule_removal()

        await context.bot.send_message(
            chat_id=data["chat_id"],
            text=f"🚨 *MINT IS LIVE!* Firing now...\n\n`{contract_address}`",
            parse_mode="Markdown"
        )

        result = await minter.mint(
            contract_address=contract_address,
            quantity=data["quantity"],
            max_price_eth=data["max_price_eth"]
        )

        if result["success"]:
            await context.bot.send_message(
                chat_id=data["chat_id"],
                text=f"✅ *Auto-Mint Successful!*\n\n"
                     f"🔗 TX: `{result['tx_hash']}`\n"
                     f"💸 Cost: {result['total_cost_eth']:.6f} ETH\n\n"
                     f"[View on BaseScan](https://basescan.org/tx/{result['tx_hash']})",
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(
                chat_id=data["chat_id"],
                text=f"❌ *Auto-Mint Failed*\n\nReason: {result['error']}",
                parse_mode="Markdown"
            )


# ============================================================
# /balance — Check your wallet ETH balance
# ============================================================
@only_me
async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = await minter.get_balance()
    await update.message.reply_text(
        f"💰 *Wallet Balance*\n\n"
        f"`{minter.wallet_address[:6]}...{minter.wallet_address[-4:]}`\n\n"
        f"ETH (Base): `{bal['eth']:.6f} ETH`\n"
        f"USD (approx): `${bal['usd_approx']:.2f}`",
        parse_mode="Markdown"
    )


# ============================================================
# /gas — Check current Base gas prices
# ============================================================
@only_me
async def gas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gas = await minter.get_gas_price()
    await update.message.reply_text(
        f"⛽ *Current Gas (Base)*\n\n"
        f"Slow: `{gas['slow']} gwei`\n"
        f"Standard: `{gas['standard']} gwei`\n"
        f"Fast: `{gas['fast']} gwei`",
        parse_mode="Markdown"
    )


# ============================================================
# /jobs — Show active watch jobs
# ============================================================
@only_me
async def jobs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jobs = context.bot_data.get("watch_jobs", {})
    if not jobs:
        await update.message.reply_text("📋 No active watch jobs.")
        return

    text = "📋 *Active Watch Jobs*\n\n"
    for addr in jobs:
        text += f"• `{addr[:6]}...{addr[-4:]}`\n"

    text += f"\n_Use /stopwatch <address> to cancel_"
    await update.message.reply_text(text, parse_mode="Markdown")


# ============================================================
# /stopwatch <contract_address> — Cancel a watch job
# ============================================================
@only_me
async def stopwatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/stopwatch <contract_address>`", parse_mode="Markdown")
        return

    addr = args[0]
    jobs = context.bot_data.get("watch_jobs", {})

    if addr in jobs:
        jobs[addr].schedule_removal()
        del jobs[addr]
        await update.message.reply_text(f"🛑 Stopped watching `{addr[:6]}...{addr[-4:]}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ No active watch for that address.")


# ============================================================
# Button callbacks (from inline keyboard)
# ============================================================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "balance":
        bal = await minter.get_balance()
        await query.edit_message_text(
            f"💰 *Wallet Balance*\n\n"
            f"ETH (Base): `{bal['eth']:.6f} ETH`\n"
            f"USD (approx): `${bal['usd_approx']:.2f}`",
            parse_mode="Markdown"
        )
    elif query.data == "gas":
        gas = await minter.get_gas_price()
        await query.edit_message_text(
            f"⛽ *Current Gas (Base)*\n\n"
            f"Slow: `{gas['slow']} gwei`\n"
            f"Standard: `{gas['standard']} gwei`\n"
            f"Fast: `{gas['fast']} gwei`",
            parse_mode="Markdown"
        )
    elif query.data == "mint_menu":
        await query.edit_message_text(
            "💎 *Mint an NFT*\n\n"
            "Send me the command:\n"
            "`/mint <contract_address> [quantity] [max_price_eth]`\n\n"
            "*Example:*\n`/mint 0xAbc123... 1 0.005`\n\n"
            "Or to auto-mint when a drop goes live:\n"
            "`/watch <contract_address>`",
            parse_mode="Markdown"
        )
    elif query.data == "jobs":
        jobs = context.bot_data.get("watch_jobs", {})
        if not jobs:
            await query.edit_message_text("📋 No active watch jobs.")
        else:
            text = "📋 *Active Watch Jobs*\n\n"
            for addr in jobs:
                text += f"• `{addr[:6]}...{addr[-4:]}`\n"
            await query.edit_message_text(text, parse_mode="Markdown")


# ============================================================
# MAIN — Start the bot
# ============================================================
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Register all commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mint", mint_command))
    app.add_handler(CommandHandler("watch", watch_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("gas", gas_command))
    app.add_handler(CommandHandler("jobs", jobs_command))
    app.add_handler(CommandHandler("stopwatch", stopwatch_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    log.info("🤖 Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
