"""Localization utility for Q-Bot backend.

Centralizes language mappings and localized standard messages across
state machines, agents, and routes.
"""

from typing import Dict, Any, List

LANGUAGE_MAP = {
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil",
}

def get_language_name(lang_code: str) -> str:
    """Get the human-readable name for a language code."""
    return LANGUAGE_MAP.get(lang_code, "English")

# Standard localized messages
MESSAGES = {
    "en": {
        "welcome": "Welcome! I'm here to help.",
        "ask_contact": "To get started, could you please share your name and phone number?",
        "ask_contact_for_s3International": "To get started, could you please share your name and phone number with country code. Example: +1 2025550123?",
        "got_phone_ask_name": [
            "Great, got your number! May I know your name?",
            "Thanks! And what name should I address you by?",
            "Perfect! What's your name so I can personalize our chat?",
             ],
           "need_phone_to_continue": [
               "I'll need your phone number to continue. What number can I reach you at?",
                "To help you better, could you share your phone number with me?",
                "What's a good number to reach you? I'd like to follow up on this.",
                "Mind sharing your phone number? It helps me assist you better?",
           ],
           "need_phone_to_continue_for_s3International": [
               "I'll need your phone number with country code to continue. What number can I reach you at?",
               "To help you better, could you share your phone number with me? (Enter with country code)",
               "What's a good number to reach you? I'd like to follow up on this.(Enter with country code)",
               "Mind sharing your phone number? It helps me assist you better? (Enter with country code)", ],
           "phone_ask_with_name": ["Thanks, {name}! What's the best phone number to reach you at?",
                                   "Great, {name}! Could you share your phone number?",
                                   "Perfect, {name}! What number should I use to contact you?", ],
           "phone_ask_with_name_for_s3International": [
               "Thanks, {name}! What's the best phone number to reach you at? (Enter with country code)",
               "Great, {name}! Could you share your phone number with country code. Example: +1 2025550123?",
               "Perfect, {name}! What number should I use to contact you? (Enter with country code)",
           ],
           "general_contact_ask": [
               "To help you better, could you please share your name and phone number?",
                "I'd love to help! Can you tell me your name and phone number?",
                "What's your name and phone number? I'll make sure to follow up with you.",
           ],
           "general_contact_ask_for_s3International": [
               "To help you better, could you please share your name and phone number with country code?",
               "I'd love to help! Can you tell me your name and phone number with country code. Example: +1 2025550123?",
               "What's your name and phone number? I'll make sure to follow up with you. (Enter with country code)",
           ],
           "evasion_fallback": {
               "resp": "I notice we've been going back and forth a bit! 😊 No worries — I just need a few details to help you better. Would you like to continue with the qualification, or would you prefer to speak with our team directly?",
               "qr": ["Continue", "Talk to team"]},
           "technical_error": "I apologize, I'm having a brief technical difficulty. Could you try sending your message again?",
           "disinterested": "I completely understand, {name}. I won't take up any more of your time! 😊\n\nI'll close this chat for now. If you ever change your mind or need any information in the future, we'll be right here. Have a truly wonderful day!",
           "qualified": "Thank you so much for your time, {name}! 🎉\n\nBased on our conversation, I've gathered all the information we need. Our team will reach out to you shortly to discuss the next steps.\n\nHere's a summary of what we discussed:",
           "disqualified": "Thank you for chatting with us, {name}! 😊\n\nI appreciate you sharing your requirements. While we may not be the perfect fit right now, I've saved your details and our team may follow up if something aligns better in the future.",
           "summary": {"name": "Name", "phone": "Phone", "not_provided": "Not provided",
                       "intro": "Thank you! Here's what I've noted:",
                       "outro": "Would you like to schedule a call with our team?"}, "mapping_summary": {
            "high": "Thanks{name_part}! 🎉 Based on our conversation, you look like a great fit. {company} will be in touch with you very shortly!",
            "medium": "Thanks{name_part}! 😊 You've shared some great information. {company} will review your details and reach out soon.",
            "low": "Thanks{name_part} for taking the time to share your details. Our team will be in touch to discuss how we can best help you."},
           "mapping_ended": "Thank you! Our team has your details and will be in touch soon. 😊",
           "mapping_transition": {"no_phone": "No problem, let's move on to a few quick questions",
                                  "thanks": "Thank you{name_part}! 😊 Let me ask you a few quick questions",
                                  "suffix": "to understand your needs better."}},
    "hi": {
        "welcome": "नमस्ते! मैं आपकी सहायता के लिए यहाँ हूँ।",

        "ask_contact": "शुरू करने के लिए, क्या आप कृपया अपना नाम और फ़ोन नंबर साझा कर सकते हैं?",
        "ask_contact_for_s3International": "शुरू करने के लिए, क्या आप कृपया अपना नाम और देश कोड के साथ फ़ोन नंबर साझा कर सकते हैं? उदाहरण: +91 9876543210",

        "got_phone_ask_name": [
            "बहुत अच्छा, आपका नंबर मिल गया! क्या मैं आपका नाम जान सकता हूँ?",
            "धन्यवाद! मैं आपको किस नाम से संबोधित करूँ?",
            "बढ़िया! आपका नाम क्या है ताकि मैं हमारी बातचीत को व्यक्तिगत बना सकूँ?"
        ],

        "need_phone_to_continue": [
            "जारी रखने के लिए मुझे आपके फ़ोन नंबर की आवश्यकता होगी। मैं आपसे किस नंबर पर संपर्क कर सकता हूँ?",
            "आपकी बेहतर सहायता के लिए, क्या आप अपना फ़ोन नंबर साझा कर सकते हैं?",
            "आपसे संपर्क करने के लिए एक अच्छा नंबर क्या है? मैं आगे फॉलो-अप करना चाहूँगा।",
            "अपना फ़ोन नंबर साझा करेंगे? इससे मुझे आपकी बेहतर सहायता करने में मदद मिलेगी।"
        ],

        "need_phone_to_continue_for_s3International": [
            "जारी रखने के लिए मुझे देश कोड के साथ आपका फ़ोन नंबर चाहिए। मैं आपसे किस नंबर पर संपर्क कर सकता हूँ?",
            "आपकी बेहतर सहायता के लिए, क्या आप देश कोड के साथ अपना फ़ोन नंबर साझा कर सकते हैं?",
            "आपसे संपर्क करने के लिए एक अच्छा नंबर क्या है? मैं आगे फॉलो-अप करना चाहूँगा। (देश कोड सहित)",
            "अपना फ़ोन नंबर साझा करेंगे? कृपया देश कोड जोड़ें ताकि मैं बेहतर सहायता कर सकूँ।"
        ],

        "phone_ask_with_name": [
            "धन्यवाद, {name}! आपसे संपर्क करने के लिए सबसे अच्छा फ़ोन नंबर क्या है?",
            "बहुत अच्छा, {name}! क्या आप अपना फ़ोन नंबर साझा कर सकते हैं?",
            "बढ़िया, {name}! मुझे आपसे संपर्क करने के लिए किस नंबर का उपयोग करना चाहिए?"
        ],

        "phone_ask_with_name_for_s3International": [
            "धन्यवाद, {name}! आपसे संपर्क करने के लिए सबसे अच्छा फ़ोन नंबर क्या है? (देश कोड के साथ)",
            "बहुत अच्छा, {name}! क्या आप अपना फ़ोन नंबर देश कोड के साथ साझा कर सकते हैं? उदाहरण: +91 9876543210",
            "बढ़िया, {name}! मुझे आपसे संपर्क करने के लिए किस नंबर का उपयोग करना चाहिए? (देश कोड सहित)"
        ],

        "general_contact_ask": [
            "आपकी बेहतर सहायता के लिए, क्या आप कृपया अपना नाम और फ़ोन नंबर साझा कर सकते हैं?",
            "मुझे आपकी मदद करने में खुशी होगी! क्या आप अपना नाम और फ़ोन नंबर बता सकते हैं?",
            "आपका नाम और फ़ोन नंबर क्या है? मैं सुनिश्चित करूँगा कि हम आपसे संपर्क करें।"
        ],

        "general_contact_ask_for_s3International": [
            "आपकी बेहतर सहायता के लिए, क्या आप कृपया अपना नाम और देश कोड के साथ फ़ोन नंबर साझा कर सकते हैं?",
            "मुझे आपकी मदद करने में खुशी होगी! क्या आप अपना नाम और देश कोड के साथ फ़ोन नंबर बता सकते हैं? उदाहरण: +91 9876543210",
            "आपका नाम और फ़ोन नंबर क्या है? मैं सुनिश्चित करूँगा कि हमारी टीम आपसे संपर्क करे। (देश कोड सहित)"
        ],

        "evasion_fallback": {
            "resp": "मैं देख रहा हूँ कि हम थोड़ा आगे-पीछे हो रहे हैं! 😊 कोई बात नहीं — आपकी बेहतर सहायता के लिए मुझे बस कुछ जानकारी चाहिए। क्या आप प्रक्रिया जारी रखना चाहेंगे, या हमारी टीम से सीधे बात करना पसंद करेंगे?",
            "qr": ["जारी रखें", "टीम से बात करें"]
        },

        "technical_error": "मुझे खेद है, मुझे थोड़ी तकनीकी समस्या हो रही है। क्या आप कृपया अपना संदेश फिर से भेज सकते हैं?",

        "disinterested": "मैं पूरी तरह समझता हूँ, {name}। मैं आपका समय और नहीं लूँगा! 😊\n\nमैं इस चैट को अभी के लिए बंद कर रहा हूँ। अगर आप कभी अपना विचार बदलते हैं या भविष्य में मदद चाहिए, तो हम यहाँ रहेंगे। आपका दिन शुभ हो!",

        "qualified": "आपके समय के लिए बहुत-बहुत धन्यवाद, {name}! 🎉\n\nहमारी बातचीत के आधार पर, मैंने सभी आवश्यक जानकारी एकत्र कर ली है। हमारी टीम जल्द ही अगले चरण के लिए आपसे संपर्क करेगी।\n\nयह रहा हमारी बातचीत का सारांश:",

        "disqualified": "हमारे साथ चैट करने के लिए धन्यवाद, {name}! 😊\n\nमैं आपके द्वारा साझा की गई जानकारी की सराहना करता हूँ। भले ही हम अभी पूरी तरह उपयुक्त न हों, हमने आपके विवरण सुरक्षित कर लिए हैं और भविष्य में उपयुक्त अवसर होने पर आपसे संपर्क किया जा सकता है।",

        "summary": {
            "name": "नाम",
            "phone": "फ़ोन नंबर",
            "not_provided": "प्रदान नहीं किया गया",
            "intro": "धन्यवाद! यहाँ मैंने जो नोट किया है:",
            "outro": "क्या आप हमारी टीम के साथ कॉल शेड्यूल करना चाहेंगे?"
        },

        "mapping_summary": {
            "high": "धन्यवाद{name_part}! 🎉 हमारी बातचीत के आधार पर, आप एक बेहतरीन फिट लगते हैं। {company} जल्द ही आपसे संपर्क करेगा!",
            "medium": "धन्यवाद{name_part}! 😊 आपने अच्छी जानकारी साझा की है। {company} आपके विवरणों की समीक्षा करेगा और जल्द ही संपर्क करेगा।",
            "low": "धन्यवाद{name_part}, अपना विवरण साझा करने के लिए समय निकालने के लिए। हमारी टीम आपसे संपर्क करेगी और आगे की मदद पर चर्चा करेगी।"
        },

        "mapping_ended": "धन्यवाद! हमारी टीम के पास आपका विवरण है और वे जल्द ही आपसे संपर्क करेंगे। 😊",

        "mapping_transition": {
            "no_phone": "कोई बात नहीं, चलिए कुछ त्वरित प्रश्नों पर आगे बढ़ते हैं",
            "thanks": "धन्यवाद{name_part}! 😊 मैं आपकी आवश्यकताओं को बेहतर समझने के लिए कुछ त्वरित प्रश्न पूछता हूँ",
            "suffix": "आपकी आवश्यकताओं को बेहतर समझने के लिए।"
        }
    },
    "ta": {
        "welcome": "வணக்கம்! நான் உங்களுக்கு உதவ இங்கே இருக்கிறேன்.",
        "ask_contact": "தொடங்குவதற்கு, உங்கள் பெயர் மற்றும் தொலைபேசி எண்ணைப் பகிர முடியுமா?",
        "got_phone_ask_name": [
            "சிறப்பு, உங்கள் எண் கிடைத்துவிட்டது! உங்கள் பெயரை நான் தெரிந்து கொள்ளலாமா?",
            "நன்றி! நான் உங்களை எந்தப் பெயரில் அழைக்க வேண்டும்?",
            "அற்புதம்! உங்கள் பெயர் என்ன, அதனால் நான் எங்கள் அரட்டையைத் தனிப்பயனாக்க முடியும்?"
        ],
        "need_phone_to_continue": [
            "தொடர எனக்கு உங்கள் தொலைபேசி எண் தேவைப்படும். நான் உங்களை எந்த எண்ணில் தொடர்பு கொள்ளலாம்?",
            "உங்களுக்குச் சிறப்பாக உதவ, உங்கள் தொலைபேசி எண்ணை என்னுடன் பகிர்ந்து கொள்ள முடியுமா?",
            "உங்களைத் தொடர்பு கொள்ள ஒரு நல்ல எண் எது? நான் இதைப் பின்தொடர விரும்புகிறேன்.",
            "உங்கள் தொலைபேசி எண்ணைப் பகிரலாமா? இது உங்களுக்குச் சிறப்பாக உதவ எனக்கு உதவுகிறது."
        ],
        "phone_ask_with_name": [
            "நன்றி, {name}! உங்களைத் தொடர்பு கொள்ள சிறந்த தொலைபேசி எண் எது?",
            "சிறப்பு, {name}! உங்கள் தொலைபேசி எண்ணைப் பகிர்ந்து கொள்ள முடியுமா?",
            "அற்புதம், {name}! உங்களைத் தொடர்பு கொள்ள நான் எந்த எண்ணைப் பயன்படுத்த வேண்டும்?"
        ],
        "general_contact_ask": [
            "உங்களுக்குச் சிறப்பாக உதவ, உங்கள் பெயர் மற்றும் தொலைபேசி எண்ணைப் பகிர முடியுமா?",
            "உங்களுக்கு உதவ நான் விரும்பிறேன! உங்கள் பெயர் மற்றும் தொலைபேசி எண்ணை என்னிடம் சொல்ல முடியுமா?",
            "உங்கள் பெயர் மற்றும் தொலைபேசி எண் என்ன? நான் நிச்சயமாக உங்களைப் பின்தொடர்வேன்."
        ],
        "evasion_fallback": {
            "resp": "நாம் சற்று முன்னும் பின்னுமாகச் செல்வதை நான் கவனிக்கிறேன்! 😊 கவலைப்பட வேண்டாம் — உங்களுக்குச் சிறப்பாக உதவ எனக்குச் சில விவரங்கள் தேவை. நீங்கள் தகுதியுடன் தொடர விரும்புகிறீர்களா அல்லது எங்கள் குழுவுடன் நேரடியாகப் பேச விரும்புகிறீர்களா?",
            "qr": ["தொடரவும்", "குழுவுடன் பேசுங்கள்"]
        },
        "technical_error": "மன்னிக்கவும், எனக்கு ஒரு சிறிய தொழில்நுட்ப சிக்கல் உள்ளது. உங்கள் செய்தியை மீண்டும் அனுப்ப முயற்சிக்கிறீர்களா?",
        "disinterested": "நான் முழுமையாகப் புரிந்துகொள்கிறேன், {name}. நான் உங்கள் நேரத்தை இனி எடுத்துக்கொள்ள மாட்டேன்! 😊\n\nஇப்போதைக்கு இந்த அரட்டையை மூடுகிறேன். நீங்கள் எப்போதாவது உங்கள் மனதை மாற்றினால் அல்லது எதிர்காலத்தில் ஏதேனும் தகவல் தேவைப்பட்டால், நாங்கள் இங்கேயே இருப்போம். ஒரு சிறந்த நாளைக் கொண்டாடுங்கள்!",
        "qualified": "உங்கள் நேரத்திற்கு மிக்க நன்றி, {name}! 🎉\n\nஎங்கள் உரையாடலின் அடிப்படையில், எங்களுக்குத் தேவையான அனைத்துத் தகவல்களையும் நான் சேகரித்துள்ளேன். அடுத்த கட்டங்களைப் பற்றி விவாதிக்க எங்கள் குழு விரைவில் உங்களைத் தொடர்பு கொள்ளும்.\n\nநாங்கள் விவாதித்தவற்றின் சுருக்கம் இங்கே உள்ளது:",
        "disqualified": "எங்களுடன் அரட்டை அடித்ததற்கு நன்றி, {name}! 😊\n\nஉங்கள் தேவைகளைப் பகிர்ந்து கொண்டதற்கு நான் உங்களைப் பாராட்டுகிறேன். இப்போது நாங்கள் சரியான பொருத்தமாக இல்லாவிட்டாலும், உங்கள் விவரங்களை நான் சேமித்துள்ளேன், எதிர்காலத்தில் ஏதேனும் சிறப்பாக இருந்தால் எங்கள் குழு பின்தொடரலாம்.",
        "summary": {
            "name": "பெயர்",
            "phone": "தொலைபேசி",
            "not_provided": "வழங்கப்படவில்லை",
            "intro": "நன்றி! நான் குறிப்பிட்டவை இங்கே உள்ளன:",
            "outro": "எங்கள் குழுவுடன் ஒரு அழைப்பைத் திட்டமிட விரும்புகிறீர்களா?"
        },
        "mapping_summary": {
            "high": "நன்றி{name_part}! 🎉 எமது உரையாடலின் அடிப்படையில், நீங்கள் மிகவும் பொருத்தமானவராகத் தெரிகிறீர்கள். {company} மிக விரைவில் உங்களைத் தொடர்பு கொள்ளும்!",
            "medium": "நன்றி{name_part}! 😊 நீங்கள் சில சிறந்த தகவல்களைப் பகிர்ந்துள்ளீர்கள். {company} உங்கள் விவரங்களை மதிப்பாய்வு செய்து விரைவில் உங்களைத் தொடர்பு கொள்ளும்.",
            "low": "நன்றி{name_part} உங்கள் விவரங்களைப் பகிர நேரம் ஒதுக்கியமைக்கு. நாங்கள் உங்களுக்கு எவ்வாறு சிறப்பாக உதவ முடியும் என்பதைப் பற்றி விவாதிக்க எங்கள் குழு உங்களைத் தொடர்பு கொள்ளும்."
        },
        "mapping_ended": "நன்றி! எங்கள் குழுவிடம் உங்கள் விவரங்கள் உள்ளன, விரைவில் உங்களைத் தொடர்பு கொள்வோம். 😊",
        "mapping_transition": {
            "no_phone": "பரவாயில்லை, சில விரைவான கேள்விகளுக்குச் செல்வோம்",
            "thanks": "நன்றி{name_part}! 😊 உங்கள் தேவைகளை சிறப்பாகப் புரிந்துகொள்ள சில விரைவான கேள்விகளைக் கேட்க அனுமதிக்கவும்",
            "suffix": "உங்கள் தேவைகளை சிறப்பாகப் புரிந்துகொள்ள."
        }
    }
}

def get_message(lang_code: str, key: str, **kwargs) -> Any:
    """Get a localized message by key, optionally formatting with kwargs."""
    lang_messages = MESSAGES.get(lang_code, MESSAGES["en"])
    message = lang_messages.get(key, MESSAGES["en"].get(key))
    
    if isinstance(message, str):
        return message.format(**kwargs)
    elif isinstance(message, list):
        return [m.format(**kwargs) for m in message]
    return message
