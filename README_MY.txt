TikTok Video + Photo Downloader ပြင်ဆင်ထားသော main.py

ပြင်ဆင်ချက်များ
- TikTok Photo Mode / Carousel အတွက် gallery-dl ကို ထည့်ထားသည်။
- Photo တစ်ပုံဆို reply_photo ဖြင့် ပို့သည်။
- Photo အများကြီးဆို Telegram Album (တစ်ကြိမ်လျှင် 10 ပုံအထိ) ဖြင့် ပို့သည်။
- Photo မတွေ့လျှင် မူလ yt-dlp Video Download ကို fallback လုပ်သည်။
- မူလ Admin commands, SQLite user tracking, ban/unban, broadcast စသည်တို့ကို ထိန်းထားသည်။

Update လုပ်ရန်
1. မူလ project ထဲက main.py ကို backup လုပ်ပါ။
2. ဒီ ZIP ထဲက main.py ကို မူလ main.py နေရာမှာ အစားထိုးပါ။
3. requirements.txt ထဲက package များ install/update လုပ်ပါ။
   pip install -r requirements.txt
4. Local မှာ စမ်းသပ်ပြီးမှ Hosting ကို redeploy လုပ်ပါ။

Hosting သတိပြုရန်
- gallery-dl ကို install လုပ်နိုင်သော Python environment လိုအပ်သည်။
- EthioDeploy ရဲ့ build/install command မှာ requirements.txt ကို install လုပ်ထားရမည်။
- Existing environment variables (BOT_TOKEN, ADMIN_IDS, DB_PATH, DOWNLOAD_TIMEOUT) ကို မဖျက်ပါနှင့်။
- ပုံတစ်ပုံချင်းစီ 49 MB ထက်ကြီးလျှင် Telegram သို့ မပို့ပါ။
- TikTok က login, region, rate limit သို့မဟုတ် API changes ကြောင့် အချို့ link များ မရနိုင်ပါ။
- ကိုယ်ပိုင် သို့မဟုတ် download ခွင့်ရှိသော content များအတွက်သာ အသုံးပြုပါ။
