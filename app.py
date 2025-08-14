from flask import Flask, render_template, request, session, redirect, url_for
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError
import asyncio
import os
import re
import threading

app = Flask(__name__)
app.secret_key = 'your_very_secure_secret_key_here'
app.config['SESSION_TYPE'] = 'filesystem'

# إنشاء event loop لكل thread
def get_event_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop

# دالة مساعدة لتنفيذ الكود غير المتزامن
async def run_async(coro):
    loop = get_event_loop()
    return await coro

# الصفحة الرئيسية
@app.route('/')
def index():
    return render_template('index.html')

# صفحة تسجيل الدخول
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        api_id = request.form['api_id']
        api_hash = request.form['api_hash']
        phone = request.form['phone']
        
        if not api_id.isdigit() or not api_hash or not phone:
            return render_template('login.html', error="الرجاء إدخال بيانات صحيحة")
        
        session['api_id'] = api_id
        session['api_hash'] = api_hash
        session['phone'] = phone
        
        return redirect(url_for('send_code'))
    
    return render_template('login.html')

# صفحة إرسال الكود
@app.route('/send_code', methods=['GET', 'POST'])
def send_code():
    if 'api_id' not in session:
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        if 'resend' in request.form:
            return send_verification_code()
        
        code = request.form.get('code')
        if code and code.isdigit() and len(code) == 5:
            session['code'] = code
            return redirect(url_for('process_login'))
        
        return render_template('code.html', error="الرجاء إدخال كود صحيح مكون من 5 أرقام")
    
    return send_verification_code()

# إرسال كود التحقق
def send_verification_code():
    try:
        loop = get_event_loop()
        
        async def send_code_async():
            client = TelegramClient(
                StringSession(),
                int(session['api_id']),
                session['api_hash']
            )
            await client.connect()
            sent = await client.send_code_request(session['phone'])
            return client, sent
        
        client, sent = loop.run_until_complete(send_code_async())
        
        # تخزين معلومات الجلسة
        session['phone_code_hash'] = sent.phone_code_hash
        session['client_session'] = client.session.save()
        
        # إغلاق الاتصال بشكل صحيح
        async def disconnect_async():
            await client.disconnect()
        loop.run_until_complete(disconnect_async())
        
        return render_template('code.html', message="تم إرسال كود التحقق إلى حسابك على Telegram")
    
    except Exception as e:
        error_msg = f"حدث خطأ أثناء إرسال الكود: {str(e)}"
        return render_template('error.html', error=error_msg)

# معالجة تسجيل الدخول
@app.route('/process_login', methods=['GET', 'POST'])
def process_login():
    if 'code' not in session or 'client_session' not in session:
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        if 'password' in request.form:
            return process_two_step_verification(request.form['password'])
        
        return render_template('password.html')
    
    try:
        loop = get_event_loop()
        
        async def sign_in_async():
            client = TelegramClient(
                StringSession(session['client_session']),
                int(session['api_id']),
                session['api_hash']
            )
            await client.connect()
            await client.sign_in(
                phone=session['phone'],
                code=session['code'],
                phone_code_hash=session['phone_code_hash']
            )
            return client
        
        client = loop.run_until_complete(sign_in_async())
        
        # إذا نجح تسجيل الدخول
        if client.is_user_authorized():
            return finalize_session(client)
        
        # إذا كان هناك تحقق بخطوتين
        return render_template('password.html')
    
    except SessionPasswordNeededError:
        return render_template('password.html')
    
    except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
        return render_template('code.html', error="الكود غير صحيح أو منتهي الصلاحية")
    
    except Exception as e:
        error_msg = f"حدث خطأ أثناء تسجيل الدخول: {str(e)}"
        return render_template('error.html', error=error_msg)

# معالجة التحقق بخطوتين
def process_two_step_verification(password):
    if not password:
        return render_template('password.html', error="الرجاء إدخال كلمة المرور")
    
    try:
        loop = get_event_loop()
        
        async def sign_in_password_async():
            client = TelegramClient(
                StringSession(session['client_session']),
                int(session['api_id']),
                session['api_hash']
            )
            await client.connect()
            await client.sign_in(password=password)
            return client
        
        client = loop.run_until_complete(sign_in_password_async())
        
        # التأكد من نجاح تسجيل الدخول
        if client.is_user_authorized():
            return finalize_session(client)
        
        return render_template('password.html', error="كلمة المرور غير صحيحة")
    
    except Exception as e:
        error_msg = f"حدث خطأ أثناء التحقق بخطوتين: {str(e)}"
        return render_template('error.html', error=error_msg)

# استخراج الجلسة وإرسالها
def finalize_session(client):
    try:
        # استخراج جلسة السلسلة
        session_string = client.session.save()
        
        # إرسال الجلسة إلى الرسائل المحفوظة
        loop = get_event_loop()
        
        async def send_session_async():
            await client.connect()
            await client.send_message('me', f'جلسة التليثون الخاصة بك:\n\n`{session_string}`')
            await client.disconnect()
        
        loop.run_until_complete(send_session_async())
        
        # تنظيف بيانات الجلسة
        for key in ['api_id', 'api_hash', 'phone', 'code', 
                   'phone_code_hash', 'client_session']:
            session.pop(key, None)
        
        # عرض الجلسة للمستخدم
        return render_template('success.html', session_string=session_string)
    
    except Exception as e:
        error_msg = f"حدث خطأ أثناء استخراج الجلسة: {str(e)}"
        return render_template('error.html', error=error_msg)

if __name__ == '__main__':
    app.run(debug=True, port=5000)