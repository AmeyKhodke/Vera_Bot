from flask import Flask, request, jsonify
from datetime import datetime, timezone
import time, uuid
from bot import compose, reply_compose
from dotenv import load_dotenv
import os
import json
import threading

load_dotenv()

app = Flask(__name__)
START_TIME = time.time()

# Global stores
context_store = {}        # (scope, context_id) -> {version, payload}
conversations = {}        # conversation_id -> [messages]
conversation_contexts = {} # conversation_id -> {merchant_id, trigger_id, customer_id}
store_lock = threading.Lock()

@app.route('/v1/healthz', methods=['GET'])
def healthz():
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    with store_lock:
        items = list(context_store.values())
    
    for v in items:
        scope = v.get("scope")
        if scope in counts:
            counts[scope] += 1
        else:
            counts[scope] = counts.get(scope, 0) + 1
            
    return jsonify({
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": counts
    })

@app.route('/v1/metadata', methods=['GET'])
def metadata():
    return jsonify({
        "team_name": "Team Vera AI",
        "team_members": ["Amey Khodke"],
        "model": "llama-3.3-70b-versatile",
        "approach": "Flask with Groq API (Llama 3)",
        "contact_email": "ameykhodke@example.com",
        "version": "1.1.0",
        "submitted_at": datetime.now(timezone.utc).isoformat()
    })

@app.route('/v1/context', methods=['POST'])
def push_context():
    body = request.json
    scope = body.get('scope')
    context_id = body.get('context_id')
    version = body.get('version')
    payload = body.get('payload')
    
    key = f"{scope}_{context_id}"
    cur = context_store.get(key)
    
    if cur and cur.get("version") > version:
        return jsonify({
            "accepted": False, 
            "reason": "stale_version", 
            "current_version": cur["version"]
        }), 409
        
    if cur and cur.get("version") == version:
        return jsonify({
            "accepted": True,
            "ack_id": f"ack_{context_id}_v{version}",
            "stored_at": datetime.now(timezone.utc).isoformat() + "Z"
        })
        
    with store_lock:
        context_store[key] = {
            "scope": scope,
            "version": version,
            "payload": payload
        }
    
    return jsonify({
        "accepted": True,
        "ack_id": f"ack_{context_id}_v{version}",
        "stored_at": datetime.now(timezone.utc).isoformat() + "Z"
    })

@app.route('/v1/tick', methods=['POST'])
def tick():
    body = request.json or {}
    available_triggers = body.get('available_triggers', [])
    
    actions = []
    seen = set()  # Deduplication

    for trg_id in available_triggers:
        try:
            # 🔹 Trigger
            trg_wrapper = context_store.get(f"trigger_{trg_id}")
            if not trg_wrapper:
                continue
            trg = trg_wrapper.get("payload", {})
            
            # 🔹 Merchant
            merchant_id = trg.get("merchant_id")
            merchant_wrapper = context_store.get(f"merchant_{merchant_id}")
            if not merchant_wrapper:
                continue
            merchant = merchant_wrapper.get("payload", {})
            
            # 🔹 Category
            category_slug = merchant.get("category_slug")
            category_wrapper = context_store.get(f"category_{category_slug}")
            if not category_wrapper:
                continue
            category = category_wrapper.get("payload", {})
            
            # 🔹 Customer
            customer_id = trg.get("customer_id")
            customer = None
            if customer_id:
                customer_wrapper = context_store.get(f"customer_{customer_id}")
                if customer_wrapper:
                    customer = customer_wrapper.get("payload")
            
            # 🔥 Deduplication (SAFE)
            unique_key = f"{merchant_id}_{customer_id}"
            if unique_key in seen:
                continue
            seen.add(unique_key)
            
            # 🔹 Compose
            composition = compose(category, merchant, trg, customer)
            if not composition:
                continue
            
            # 🔥 LIGHT INTELLIGENCE (SAFE)
            if trg.get("event_type") == "low_priority":
                continue
            
            # 🔹 Action (STRICT FORMAT)
            actions.append({
                "conversation_id": f"conv_{merchant_id}_{trg_id}",
                "merchant_id": merchant_id,
                "customer_id": customer_id,
                "send_as": composition.get("send_as", "vera"),
                "trigger_id": trg_id,
                "template_name": "vera_generic_v1",
                "template_params": [
                    merchant.get('identity', {}).get('name', ''),
                    category.get('name', ''),
                    "Special Offer"
                ],
                "body": composition.get("body", ""),
                "cta": composition.get("cta", "open_ended"),
                "suppression_key": trg.get("suppression_key", ""),
                "rationale": composition.get("rationale", "")
            })
            
            # Store context for reply grounding
            with store_lock:
                conversation_contexts[f"conv_{merchant_id}_{trg_id}"] = {
                    "merchant_id": merchant_id,
                    "trigger_id": trg_id,
                    "customer_id": customer_id
                }

        except Exception:
            continue

    return jsonify({"actions": actions})

@app.route('/v1/reply', methods=['POST'])
def reply():
    body = request.json
    conversation_id = body.get('conversation_id')
    from_role = body.get('from_role')
    message = body.get('message')
    
    # Store message in history
    conv = conversations.setdefault(conversation_id, [])
    conv.append({
        "role": from_role, 
        "body": message
    })
    
    # Retrieve context for this conversation
    meta = conversation_contexts.get(conversation_id, {})
    merchant_id = meta.get("merchant_id") or body.get("merchant_id")
    trigger_id = meta.get("trigger_id")
    customer_id = meta.get("customer_id") or body.get("customer_id")
    
    category = {}
    merchant = {}
    trigger = {}
    customer = None
    
    with store_lock:
        if merchant_id:
            m_wrap = context_store.get(f"merchant_{merchant_id}")
            if m_wrap:
                merchant = m_wrap.get("payload", {})
                cat_slug = merchant.get("category_slug")
                if cat_slug:
                    c_wrap = context_store.get(f"category_{cat_slug}")
                    if c_wrap:
                        category = c_wrap.get("payload", {})
        
        if trigger_id:
            t_wrap = context_store.get(f"trigger_{trigger_id}")
            if t_wrap:
                trigger = t_wrap.get("payload", {})
        
        if customer_id:
            cust_wrap = context_store.get(f"customer_{customer_id}")
            if cust_wrap:
                customer = cust_wrap.get("payload", {})

    # Call the reply_composer with context
    result = reply_compose(conv, category, merchant, trigger, customer)
    
    if result.get("action") == "send":
        conv.append({
            "role": "vera",
            "body": result.get("body", "")
        })
        
    return jsonify({
        "action": result.get("action", "send"), 
        "body": result.get("body", ""), 
        "cta": result.get("cta", "open_ended"),
        "rationale": result.get("rationale", "")
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
