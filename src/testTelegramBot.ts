// Test script to verify Telegram bot functionality
import { TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID } from "./config.js";

async function testTelegramBot() {
  console.log("🧪 Testing Telegram Bot Commands...");
  
  if (!TELEGRAM_BOT_TOKEN || !TELEGRAM_CHAT_ID) {
    console.log("❌ Telegram bot token or chat ID not configured");
    return;
  }

  // Test sending a welcome message
  try {
    const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`;
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: TELEGRAM_CHAT_ID,
        text: "🤖 <b>FinalBot Test</b>\n\nYour Telegram bot is now fully configured with interactive commands!\n\nTry these commands:\n/start - Main menu\n/portfolio - View positions\n/trades - Recent trades\n/pnl - PnL report\n/status - Bot status\n/help - Help",
        parse_mode: "HTML",
        reply_markup: {
          inline_keyboard: [
            [
              { text: "📊 Portfolio", callback_data: "portfolio" },
              { text: "💰 PnL Report", callback_data: "pnl" }
            ],
            [
              { text: "🤖 Bot Status", callback_data: "status" }
            ]
          ]
        }
      })
    });

    const result = await response.json() as { ok: boolean; description?: string };
    if (result.ok) {
      console.log("✅ Test message sent successfully!");
      console.log("📱 Check your Telegram for the test message and try the buttons");
      console.log("🔄 The bot handler is now polling for commands...");
    } else {
      console.log("❌ Failed to send test message:", result.description);
    }
  } catch (error) {
    console.error("❌ Error testing Telegram bot:", error);
  }
}

testTelegramBot();
