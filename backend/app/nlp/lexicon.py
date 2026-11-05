"""Word lists of the validation rules (app/nlp/validate.py), in English, Banglish and Bangla.

To tune a rule, change its list here. Write phrases with a space: it also matches a hyphen ("user friendly"
finds "user-friendly"). Matching ignores case and works on whole words, so inflected Bangla forms are listed
one by one: a bare "কম" must not match "কমপক্ষে".
"""

# ---------- vague wording ----------

VAGUE_WORDS = {
    # English
    "fast", "faster", "fastest", "quick", "quickly", "rapid", "rapidly", "soon", "as soon as possible", "asap",
    "easy", "easily", "simple", "user friendly", "userfriendly", "friendly", "intuitive", "seamless", "seamlessly",
    "smooth", "smoothly", "nice", "beautiful", "modern", "good", "better", "best", "high quality", "high performance",
    "state of the art", "lightweight", "many", "some", "several", "few", "various", "numerous", "a lot", "lots",
    "etc", "and so on", "and/or", "flexible", "efficient", "efficiently", "robust", "scalable", "adequate",
    "appropriate", "reasonable", "sufficient", "acceptable", "minimal", "optimal", "approximately", "roughly",
    "usually", "normally", "often", "sometimes", "if possible", "if necessary", "when necessary", "as needed",
    # Banglish
    "taratari", "tara tari", "joldi", "jaldi", "druto", "shigroi", "shighroi", "shiggir", "shigghir", "sohoj",
    "sohoje", "shohoj", "shohoje", "sahaj", "onek", "anek", "kichu", "kisu", "kichhu", "koyekta", "beshi", "besi",
    "kom", "valo", "bhalo", "sundor", "shundor", "chomotkar", "motamuti", "mota muti", "prai", "praay", "ityadi",
    "ittadi", "hoyto", "shombhob hole", "sombhob hole", "somvob hole", "joto taratari shombhob", "dorkar moto",
    "proyojon moto",
    # Bangla
    "দ্রুত", "দ্রুতই", "দ্রুততম", "দ্রুতগতিতে", "তাড়াতাড়ি", "জলদি", "শীঘ্র", "শীঘ্রই", "সহজ", "সহজে", "সহজেই",
    "সহজভাবে", "অনেক", "কিছু", "কয়েক", "কয়েকটি", "কয়েকটা", "বেশি", "বেশী", "কম", "ভালো", "ভাল", "ভালোভাবে", "সুন্দর",
    "সুন্দরভাবে", "চমৎকার", "মোটামুটি", "প্রায়", "ইত্যাদি", "হয়তো", "যথেষ্ট", "উন্নত", "আধুনিক", "নমনীয়", "কার্যকর",
    "সম্ভব হলে", "যত দ্রুত সম্ভব", "যত তাড়াতাড়ি সম্ভব", "প্রয়োজনমতো", "প্রয়োজন মতো", "দরকারমতো", "দরকার মতো",
}  # fmt: skip

# ---------- missing information ----------

# Who a requirement can be for. "system" is left out on purpose: extraction starts every statement with it.
ACTOR_WORDS = {
    # English
    "user", "users", "admin", "admins", "administrator", "administrators", "customer", "customers", "client",
    "clients", "manager", "managers", "owner", "owners", "staff", "employee", "employees", "student", "students",
    "teacher", "teachers", "driver", "drivers", "rider", "riders", "seller", "sellers", "buyer", "buyers", "vendor",
    "vendors", "merchant", "merchants", "member", "members", "guest", "guests", "visitor", "visitors", "operator",
    "operators", "agent", "agents", "accountant", "accountants", "cashier", "cashiers", "doctor", "doctors",
    "patient", "patients", "supervisor", "supervisors", "moderator", "moderators", "subscriber", "subscribers",
    # Banglish
    "grahok", "kreta", "bikreta", "kormi", "chhatro", "chatro", "shikkhok", "malik", "beboharkari", "dokandar",
}  # fmt: skip

# Matched at the start of a word, so case endings count too (গ্রাহকের, অ্যাডমিনকে, ব্যবহারকারীরা)
ACTOR_WORDS_BANGLA = {
    "ব্যবহারকারী", "গ্রাহক", "অ্যাডমিন", "এডমিন", "ইউজার", "কাস্টমার", "ক্লায়েন্ট", "ম্যানেজার", "মালিক", "কর্মী", "কর্মচারী",
    "ছাত্র", "শিক্ষক", "ড্রাইভার", "ক্রেতা", "বিক্রেতা", "দোকানদার", "সদস্য", "অতিথি",
}  # fmt: skip

# Digits (also ০-৯) always count as numbers; these are the numbers written as words.
# Left out on purpose: "at" (Banglish 8) is the English word, "নয়" (Bangla 9) also means "not".
NUMBER_WORDS = {
    # English
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "twenty", "thirty", "fifty",
    "hundred", "thousand", "million",
    # Banglish
    "ek", "dui", "tin", "char", "pach", "panch", "choy", "sat", "dosh", "bish", "ponchash", "eksho", "hajar", "lakh",
    # Bangla
    "এক", "দুই", "তিন", "চার", "পাঁচ", "ছয়", "সাত", "আট", "দশ", "বিশ", "পঞ্চাশ", "একশ", "একশো", "হাজার", "লাখ",
}  # fmt: skip

UNITS = {
    # English
    "ms", "msec", "millisecond", "milliseconds", "s", "sec", "secs", "second", "seconds", "min", "mins", "minute",
    "minutes", "hr", "hrs", "hour", "hours", "day", "days", "week", "weeks", "month", "months", "year", "years",
    "am", "pm", "percent", "kb", "mb", "gb", "tb", "kbps", "mbps", "rps", "tps", "qps", "user", "users", "request",
    "requests", "transaction", "transactions", "attempt", "attempts", "time", "times", "retry", "retries",
    "character", "characters", "digit", "digits", "item", "items", "record", "records", "row", "rows", "file",
    "files", "page", "pages", "step", "steps", "click", "clicks", "concurrent", "simultaneous",
    # Banglish
    "ghonta", "ghontay", "din", "diner", "mash", "mas", "bochor", "jon", "bar", "ta", "ti", "shotangsho", "shotokora",
}  # fmt: skip

# Matched at the start of a word, so endings count too (সেকেন্ডের, ৩ দিনে, ৫টি)
UNITS_BANGLA = {
    "সেকেন্ড", "মিনিট", "ঘণ্টা", "ঘন্টা", "দিন", "সপ্তাহ", "মাস", "বছর", "শতাংশ", "জন", "বার", "টি", "টা", "অক্ষর", "এমবি", "জিবি",
}  # fmt: skip

# A number after one of these is a value even without a unit: "at most 5", "সর্বোচ্চ ৫"
COMPARATORS = {
    # English
    "at least", "at most", "up to", "no more than", "not more than", "more than", "less than", "fewer than", "under",
    "below", "over", "above", "within", "maximum", "minimum", "max", "min", "exceed", "exceeding",
    # Banglish
    "sorbochcho", "sorbonimno", "kompokkhe", "ontoto",
    # Bangla
    "সর্বোচ্চ", "সর্বনিম্ন", "সর্বাধিক", "কমপক্ষে", "অন্তত",
}  # fmt: skip

# Words that make a time or a limit expected (only checked for functional requirements)
LIMIT_WORDS = {
    # English
    "expire", "expires", "expired", "expiry", "expiration", "timeout", "timeouts", "time out", "retry", "retries",
    "reminder", "reminders", "deadline", "deadlines", "backup", "backups", "limit", "limits", "limited", "maximum",
    "minimum", "quota", "quotas", "attempt", "attempts", "upload", "uploads", "attachment", "attachments",
    "file size", "capacity",
    # Banglish
    "meyad", "shima", "sima", "sorbochcho", "sorbonimno",
}  # fmt: skip

# Matched at the start of a word
LIMIT_WORDS_BANGLA = {
    "মেয়াদ", "সময়সীমা", "সীমা", "ডেডলাইন", "রিমাইন্ডার", "ব্যাকআপ", "টাইমআউট", "সর্বোচ্চ", "সর্বনিম্ন", "কোটা", "আপলোড", "সংযুক্তি",
}  # fmt: skip
