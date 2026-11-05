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
