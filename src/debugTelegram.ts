import { TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID } from "./config.js";

async function debugTelegram() {
  console.log("🔍 Debugging Telegram setup...");
  
  // Check configuration
  console.log(`Bot Token: ${TELEGRAM_BOT_TOKEN ? "✅ Set" : "❌ Missing"}`);
  console.log(`Chat ID: ${TELEGRAM_CHAT_ID ? "✅ Set" : "❌ Missing"}`);
  
  if (!TELEGRAM_BOT_TOKEN || !TELEGRAM_CHAT_ID) {
    console.log("❌ Missing configuration");
    return;
  }
  
  console.log(`Bot Token (first 10 chars): ${TELEGRAM_BOT_TOKEN.substring(0, 10)}...`);
  console.log(`Chat ID: ${TELEGRAM_CHAT_ID}`);
  
  // Test bot info
  try {
    console.log("\n🤖 Testing bot info...");
    const botInfoUrl = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe`;
    const response = await fetch(botInfoUrl);
    const data = (await response.json()) as { ok: boolean; result?: { username: string; first_name: string }; description?: string };
    
    if (data.ok) {
      console.log(`✅ Bot: @${data.result?.username}`);
      console.log(`✅ Bot Name: ${data.result?.first_name}`);
    } else {
      console.log(`❌ Bot info failed: ${data.description}`);
      return;
    }
  } catch (err) {
    console.log(`❌ Bot info error: ${err}`);
    return;
  }
  
  // Test sending message
  try {
    console.log("\n📤 Testing message send...");
    const sendUrl = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`;
    const response = await fetch(sendUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        chat_id: TELEGRAM_CHAT_ID,
        text: "🧪 Debug test message from FinalBot!",
        parse_mode: "HTML",
      }),
    });
    
    const data = (await response.json()) as { ok: boolean; result?: { message_id: number }; description?: string; error_code?: number };
    console.log(`Response status: ${response.status}`);
    console.log(`Response data:`, JSON.stringify(data, null, 2));
    
    if (data.ok) {
      console.log("✅ Message sent successfully!");
      console.log(`Message ID: ${data.result?.message_id}`);
    } else {
      console.log(`❌ Send failed: ${data.description}`);
      console.log(`Error code: ${data.error_code}`);
      
      // Common errors and solutions
      if (data.description?.includes("chat not found")) {
        console.log("\n💡 Solution: Make sure you've sent a message to your bot first!");
        console.log("   1. Find your bot in Telegram");
        console.log("   2. Send it any message (like 'hi')");
        console.log("   3. Try this test again");
      } else if (data.description?.includes("forbidden")) {
        console.log("\n💡 Solution: Bot may be blocked or privacy settings");
        console.log("   1. Check if you blocked the bot");
        console.log("   2. Make sure bot can message you");
      }
    }
  } catch (err) {
    console.log(`❌ Send error: ${err}`);
  }
}

debugTelegram().catch(console.error);
