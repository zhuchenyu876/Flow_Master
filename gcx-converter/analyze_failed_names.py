"""分析失败的intent名称，找出共同特征"""

failed_names = [
    "RewardsAndPromotion_UsageOfRewardCashEntranceOffsetSpendingAtMerchants",
    "RewardsAndPromotion_MCRewardPlusStampCardNLUPeriodToCreditStamps",
    "RewardsAndPromotion_UsageOfRewardCashEntranceRedeemVouchersOrGifts",
    "RewardsAndPromotion_RewardCashFeaturesE5IneligibleSpendingCategories",
    "RewardsAndPromotion_MCRewardPlusStampCardNLUCheckingRegisteredPromotions",
    "RewardsAndPromotion_UsageOfRewardCashEntrancePayCreditCardBills",
    "RewardsAndPromotion_MCRewardPlusStampCardNLUCheckingRewardCashCreditRecords"
]

# 检查成功的例子
import json

with open("output/qa_knowledge_bases/kb_per_intent_results_en.json", 'r', encoding='utf-8') as f:
    data = json.load(f)

results = data.get("results", {})

# 找几个成功的名称
success_names = []
for name, record in results.items():
    if record.get("status") == "success" and record.get("kb_id"):
        success_names.append(name)
        if len(success_names) >= 10:
            break

print("="*80)
print("❌ 失败的知识库名称分析")
print("="*80)
for name in failed_names:
    print(f"\n名称: {name}")
    print(f"  长度: {len(name)} 字符")
    print(f"  包含特殊字符: {[c for c in name if not c.isalnum() and c != '_']}")

print("\n" + "="*80)
print("✅ 成功的知识库名称分析（前10个）")
print("="*80)
for name in success_names:
    print(f"\n名称: {name}")
    print(f"  长度: {len(name)} 字符")
    print(f"  包含特殊字符: {[c for c in name if not c.isalnum() and c != '_']}")

print("\n" + "="*80)
print("📊 统计对比")
print("="*80)
failed_lengths = [len(n) for n in failed_names]
success_lengths = [len(n) for n in success_names]

print(f"\n失败的名称长度:")
print(f"  最短: {min(failed_lengths)}")
print(f"  最长: {max(failed_lengths)}")
print(f"  平均: {sum(failed_lengths)/len(failed_lengths):.1f}")

print(f"\n成功的名称长度:")
print(f"  最短: {min(success_lengths)}")
print(f"  最长: {max(success_lengths)}")
print(f"  平均: {sum(success_lengths)/len(success_lengths):.1f}")

# 检查是否所有失败的都超过某个长度
threshold = 64
print(f"\n🔍 名称长度超过 {threshold} 字符:")
print(f"  失败的: {sum(1 for l in failed_lengths if l > threshold)}/{len(failed_lengths)}")
print(f"  成功的: {sum(1 for l in success_lengths if l > threshold)}/{len(success_lengths)}")

print("="*80)

