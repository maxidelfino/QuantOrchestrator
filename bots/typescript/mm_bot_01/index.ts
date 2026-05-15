import "dotenv/config";
import { createZoClient } from "../../../exchanges/typescript/zerone.js";

async function main() {
  console.log("🚀 Iniciando conexión a 01 Exchange (mainnet)...\n");

  const privateKey = process.env.PRIVATE_KEY;
  if (!privateKey) {
    throw new Error("PRIVATE_KEY no definida en .env");
  }

  const { nord, user, accountId } = await createZoClient(privateKey);

  console.log("\n📊 Mercados disponibles:");
  for (const market of nord.markets) {
    console.log(`   - ${(market as any).symbol}`);
  }

  console.log(`\n✅ Todo listo! Wallet: ${user.publicKey.toString()}`);
  console.log(`   Account ID: ${accountId}`);
  console.log(`   Balances:`, JSON.stringify(user.balances[accountId], null, 2));
}

main().catch((err) => {
  console.error("❌ Error:", err.message ?? err);
  process.exit(1);
});
