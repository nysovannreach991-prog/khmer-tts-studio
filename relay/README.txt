AI Team #1 — Relay (ម៉ាស៊ីនបកប្រែសម្រាប់អតិថិជន) · ម្ចាស់តែប៉ុណ្ណោះ
=====================================================================
អតិថិជនមិនដែលឃើញ API key ទេ: កម្មវិធី → License (AT1-…) → Relay នេះ → key របស់អ្នក (Bayon + Google)
Folder "relay" មិនត្រូវបានផ្ញើទៅអតិថិជនទេ (publish.py រំលងវា)។

ដំឡើងម្តង (ឥតគិតថ្លៃ — Cloudflare Workers: 100,000 សំណើ/ថ្ងៃ, មិនត្រូវការកាតធនាគារ)
-----------------------------------------------------------------------------
1. ចូល https://dash.cloudflare.com/sign-up → បង្កើត account (email + password) → បញ្ជាក់ email។
2. ម៉ឺនុយខាងឆ្វេង: Compute (Workers) → Workers & Pages → Create → Create Worker (Start with Hello World)
   → ដាក់ឈ្មោះ ឧ. ai-team-relay → Deploy។
3. ចុច "Edit code" → លុបកូដទាំងអស់ → បិទភ្ជាប់កូដពីឯកសារ relay\worker.js → Deploy (ខាងលើស្តាំ)។
4. ត្រឡប់ទៅ Worker → Settings → Variables and Secrets → Add:
     Type: Secret   Name: API_KEYS   Value: key ទាំងអស់ បំបែកដោយ ,
                                           ឧ. bayon_sk_aaa,bayon_sk_bbb,AIzaXXX,AIzaYYY
     Type: Text     Name: REVOKED    Value: (ទុកទទេ — ពេលក្រោយដាក់ License ID ដែលចង់បិទ)
   → Deploy។
5. ចម្លង URL នៃ Worker (ឧ. https://ai-team-relay.<ឈ្មោះអ្នក>.workers.dev)
   សាក: បើក URL នោះក្នុង Browser → ត្រូវឃើញ {"ok":true,"service":"AI Team #1 relay","keys":true}
6. ប្រាប់ Claude URL នោះ (ឬកែ update_config.json ខ្លួនឯង: "relay_url": "https://…workers.dev")
   → publish_update.bat → អតិថិជនទាំងអស់ប្រើបាន។

ប្រើប្រចាំថ្ងៃ
--------------
• បន្ថែម/ដក key: Worker → Settings → Variables and Secrets → API_KEYS → Edit → Deploy
  (មិនចាំបាច់ Update កម្មវិធីទេ · key ដែលអស់ quota / ខុស ត្រូវរំលងដោយស្វ័យប្រវត្តិ)
• បិទអតិថិជនម្នាក់: យក License ID ពី license_admin → បន្ថែមក្នុង REVOKED (បំបែកដោយ ,) → Deploy
• មើលការប្រើប្រាស់: Worker → Metrics / Logs
• អតិថិជនដែលមាន key ផ្ទាល់ខ្លួន (មិត្តភក្តិ) នៅតែប្រើ key របស់គេ — Relay ប្រើតែពេលគ្មាន key។
