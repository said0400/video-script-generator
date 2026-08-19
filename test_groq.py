"""
🔍 Groq Models Discovery Tool
اكتشاف الموديلات المتاحة فعلياً وتجربتها
"""
import os
import sys
from groq import Groq

# ─── الحصول على المفتاح ─────────────────────────
key = os.environ.get("GROQ_API_KEY", "").strip()
if not key:
    print("❌ GROQ_API_KEY not set")
    sys.exit(1)

client = Groq(api_key=key)

# ─── جلب قائمة الموديلات ────────────────────────
print("\n📋 Fetching available Groq models...\n")
try:
    models = client.models.list()
    available = sorted([m.id for m in models.data])
    
    print(f"✅ Found {len(available)} models:\n")
    for m in available:
        print(f"  • {m}")
    
except Exception as e:
    print(f"❌ Cannot list models: {e}")
    sys.exit(1)

# ─── اختبار كل موديل ─────────────────────────────
print("\n\n🧪 Testing each model with a simple prompt...\n")
print("=" * 70)

test_prompt = 'Return ONLY this JSON: {"status": "ok", "number": 42}'
working_models = []
empty_models   = []
failed_models  = []

for model_id in available:
    print(f"\n🤖 Testing: {model_id}")
    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": test_prompt}],
            max_tokens=100,
            temperature=0.3,
        )
        
        content = response.choices[0].message.content or ""
        content = content.strip()
        
        if not content:
            print(f"  ⚠️  EMPTY response")
            empty_models.append(model_id)
        else:
            preview = content[:80].replace('\n', ' ')
            print(f"  ✅ OK: {preview}...")
            working_models.append(model_id)
    
    except Exception as e:
        err = str(e)[:100]
        print(f"  ❌ FAILED: {err}")
        failed_models.append((model_id, err))

# ─── التقرير النهائي ─────────────────────────────
print("\n\n" + "=" * 70)
print("📊 FINAL REPORT")
print("=" * 70)

print(f"\n✅ WORKING MODELS ({len(working_models)}):")
for m in working_models:
    print(f"  • {m}")

if empty_models:
    print(f"\n⚠️  EMPTY RESPONSE MODELS ({len(empty_models)}):")
    for m in empty_models:
        print(f"  • {m}")

if failed_models:
    print(f"\n❌ FAILED MODELS ({len(failed_models)}):")
    for m, err in failed_models:
        print(f"  • {m}")
        print(f"    → {err[:80]}")

# ─── اقتراح MODELS_PRIORITY ─────────────────────
if working_models:
    print("\n\n" + "=" * 70)
    print("💡 SUGGESTED MODELS_PRIORITY for ai_enricher.py:")
    print("=" * 70)
    print("\nMODELS_PRIORITY = [")
    for m in working_models:
        print(f'    "{m}",')
    print("]\n")
else:
    print("\n\n❌ NO WORKING MODELS FOUND!")
    print("   Check your Groq account status and API key.")
