import os, re, sqlite3, secrets
from pathlib import Path
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from pypdf import PdfReader
from dotenv import load_dotenv

load_dotenv()
BASE = Path(__file__).resolve().parent
DB_PATH = BASE / 'data' / 'jaxn.db'
UPLOAD_DIR = BASE / 'uploads'
UPLOAD_DIR.mkdir(exist_ok=True)
ALLOWED = {'pdf'}

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY') or secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024

SYSTEM_RULES = '''You are JAXN Assist, an industrial knowledge assistant.\n\nRules:\n- Answer only from the supplied approved document excerpts.\n- Never guess or fill gaps with general knowledge.\n- If the excerpts do not clearly support an answer, say: “I could not verify this from the approved documents. Review the applicable procedure or contact the appropriate qualified person before proceeding.”\n- Never instruct a user to bypass safeguards, interlocks, lockout/tagout requirements, permits, alarms, PPE, or company authorization.\n- If sources conflict, identify the conflict.\n- For safety-critical content, be concise and conservative.\n- Cite sources as [Document name, page X].'''

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS organizations(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'employee', active INTEGER NOT NULL DEFAULT 1, FOREIGN KEY(org_id) REFERENCES organizations(id));
    CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, name TEXT NOT NULL, doc_type TEXT, filename TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'approved', uploaded_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(org_id) REFERENCES organizations(id));
    CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL, page_no INTEGER NOT NULL, content TEXT NOT NULL, FOREIGN KEY(document_id) REFERENCES documents(id));
    CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, user_id INTEGER NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL, user_id INTEGER NOT NULL, rating TEXT NOT NULL, note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    ''')
    if not conn.execute('SELECT 1 FROM organizations').fetchone():
        conn.execute('INSERT INTO organizations(name) VALUES (?)', ('JAXN Demo Industries',))
        org_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        users = [
            ('JAXN Admin','admin@jaxnassist.demo','JaxnAdmin123!','admin'),
            ('Demo Worker','worker@jaxnassist.demo','JaxnWorker123!','employee')
        ]
        for name,email,pw,role in users:
            conn.execute('INSERT INTO users(org_id,name,email,password_hash,role) VALUES (?,?,?,?,?)',(org_id,name,email,generate_password_hash(pw),role))
    conn.commit(); conn.close()

def current_user():
    if 'user_id' not in session: return None
    conn=db(); u=conn.execute('SELECT * FROM users WHERE id=? AND active=1',(session['user_id'],)).fetchone(); conn.close(); return u

def login_required(fn):
    @wraps(fn)
    def wrapped(*a,**kw):
        if not current_user(): return redirect(url_for('login'))
        return fn(*a,**kw)
    return wrapped

def admin_required(fn):
    @wraps(fn)
    def wrapped(*a,**kw):
        u=current_user()
        if not u: return redirect(url_for('login'))
        if u['role']!='admin': flash('Admin access required.','error'); return redirect(url_for('chat'))
        return fn(*a,**kw)
    return wrapped

def tokenize(text): return set(re.findall(r"[a-z0-9']+", text.lower()))

def retrieve(org_id, question, limit=6):
    q=tokenize(question)
    conn=db()
    rows=conn.execute('''SELECT c.content,c.page_no,d.name,d.id FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.org_id=? AND d.status='approved' ''',(org_id,)).fetchall(); conn.close()
    scored=[]
    for r in rows:
        words=tokenize(r['content']); score=len(q & words)
        if score: scored.append((score,r))
    scored.sort(key=lambda x:x[0], reverse=True)
    return [r for _,r in scored[:limit]]

def local_answer(question, sources):
    if not sources:
        return 'I could not verify this from the approved documents. Review the applicable procedure or contact the appropriate qualified person before proceeding.', False
    snippets=[]
    for s in sources[:4]:
        txt=' '.join(s['content'].split())
        if len(txt)>550: txt=txt[:550].rsplit(' ',1)[0]+'…'
        snippets.append(f"• {txt} [{s['name']}, page {s['page_no']}]")
    return 'I found these relevant approved passages:\n\n'+'\n\n'.join(snippets)+'\n\nDemo mode is using local retrieval. Connect an OpenAI API key for a synthesized answer grounded in these same sources.', True

def ai_answer(question, sources):
    key=os.getenv('OPENAI_API_KEY','').strip()
    if not key: return local_answer(question,sources)
    if not sources: return local_answer(question,sources)
    try:
        from openai import OpenAI
        client=OpenAI(api_key=key)
        context='\n\n'.join([f"SOURCE: {s['name']} page {s['page_no']}\n{s['content']}" for s in sources])
        resp=client.responses.create(model=os.getenv('OPENAI_MODEL','gpt-5.6-luna'), instructions=SYSTEM_RULES, input=f"QUESTION:\n{question}\n\nAPPROVED EXCERPTS:\n{context}")
        text=(getattr(resp,'output_text',None) or '').strip()
        if not text: return local_answer(question,sources)
        return text, True
    except Exception as e:
        ans,_=local_answer(question,sources)
        return ans+f"\n\nAI connection fallback: {type(e).__name__}.", True

@app.route('/')
def index(): return redirect(url_for('chat') if current_user() else url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        conn=db(); u=conn.execute('SELECT * FROM users WHERE email=? AND active=1',(request.form.get('email','').lower().strip(),)).fetchone(); conn.close()
        if u and check_password_hash(u['password_hash'],request.form.get('password','')):
            session['user_id']=u['id']; return redirect(url_for('chat'))
        flash('Invalid email or password.','error')
    return render_template('login.html')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/chat')
@login_required
def chat():
    u=current_user(); conn=db(); recent=conn.execute('SELECT * FROM questions WHERE user_id=? ORDER BY id DESC LIMIT 8',(u['id'],)).fetchall(); doc_count=conn.execute("SELECT count(*) c FROM documents WHERE org_id=? AND status='approved'",(u['org_id'],)).fetchone()['c']; conn.close()
    return render_template('chat.html', user=u, recent=recent, doc_count=doc_count)

@app.route('/api/ask', methods=['POST'])
@login_required
def ask():
    u=current_user(); payload=request.get_json(silent=True) or {}; question=(payload.get('question') or '').strip()
    if not question: return jsonify({'error':'Question is required.'}),400
    sources=retrieve(u['org_id'], question)
    answer,verified=ai_answer(question,sources)
    conn=db(); cur=conn.execute('INSERT INTO questions(org_id,user_id,question,answer,verified) VALUES (?,?,?,?,?)',(u['org_id'],u['id'],question,answer,1 if verified else 0)); qid=cur.lastrowid; conn.commit(); conn.close()
    return jsonify({'question_id':qid,'answer':answer,'verified':verified,'sources':[{'document':s['name'],'page':s['page_no']} for s in sources[:4]]})

@app.route('/api/feedback', methods=['POST'])
@login_required
def feedback():
    u=current_user(); p=request.get_json(silent=True) or {}; qid=int(p.get('question_id',0)); rating=p.get('rating','')
    if rating not in {'helpful','not_helpful'}: return jsonify({'error':'Invalid rating'}),400
    conn=db(); q=conn.execute('SELECT * FROM questions WHERE id=? AND org_id=?',(qid,u['org_id'])).fetchone()
    if not q: conn.close(); return jsonify({'error':'Question not found'}),404
    conn.execute('INSERT INTO feedback(question_id,user_id,rating,note) VALUES (?,?,?,?)',(qid,u['id'],rating,(p.get('note') or '')[:500])); conn.commit(); conn.close(); return jsonify({'ok':True})

@app.route('/admin')
@admin_required
def admin():
    u=current_user(); conn=db()
    stats={
      'questions': conn.execute('SELECT count(*) c FROM questions WHERE org_id=?',(u['org_id'],)).fetchone()['c'],
      'documents': conn.execute("SELECT count(*) c FROM documents WHERE org_id=? AND status='approved'",(u['org_id'],)).fetchone()['c'],
      'users': conn.execute('SELECT count(*) c FROM users WHERE org_id=? AND active=1',(u['org_id'],)).fetchone()['c'],
      'unverified': conn.execute('SELECT count(*) c FROM questions WHERE org_id=? AND verified=0',(u['org_id'],)).fetchone()['c']}
    docs=conn.execute('SELECT * FROM documents WHERE org_id=? ORDER BY id DESC',(u['org_id'],)).fetchall()
    qs=conn.execute('SELECT q.*,u.name user_name FROM questions q JOIN users u ON u.id=q.user_id WHERE q.org_id=? ORDER BY q.id DESC LIMIT 12',(u['org_id'],)).fetchall(); conn.close()
    return render_template('admin.html',user=u,stats=stats,documents=docs,questions=qs)

@app.route('/upload', methods=['GET','POST'])
@admin_required
def upload():
    u=current_user()
    if request.method=='POST':
        f=request.files.get('file')
        if not f or not f.filename or '.' not in f.filename or f.filename.rsplit('.',1)[1].lower() not in ALLOWED:
            flash('Please choose a PDF file.','error'); return redirect(url_for('upload'))
        filename=secure_filename(f.filename); unique=f"{secrets.token_hex(6)}_{filename}"; path=UPLOAD_DIR/unique; f.save(path)
        try:
            reader=PdfReader(str(path)); pages=[]
            for i,p in enumerate(reader.pages, start=1):
                txt=(p.extract_text() or '').strip()
                if txt: pages.append((i,txt))
            if not pages: raise ValueError('No extractable text found in PDF.')
            conn=db(); cur=conn.execute('INSERT INTO documents(org_id,name,doc_type,filename,status,uploaded_by) VALUES (?,?,?,?,?,?)',(u['org_id'],request.form.get('name') or filename,request.form.get('doc_type','Other'),unique,'approved',u['id'])); did=cur.lastrowid
            for page_no,text in pages:
                # Chunk each page into overlapping ~1400-char pieces.
                step=1150
                for start in range(0,len(text),step):
                    chunk=text[start:start+1400]
                    if chunk.strip(): conn.execute('INSERT INTO chunks(document_id,page_no,content) VALUES (?,?,?)',(did,page_no,chunk))
            conn.commit(); conn.close(); flash(f'Uploaded and indexed {len(pages)} pages.','success'); return redirect(url_for('admin'))
        except Exception as e:
            path.unlink(missing_ok=True); flash(f'Could not process PDF: {e}','error')
    return render_template('upload.html',user=u)

@app.route('/document/<int:doc_id>')
@login_required
def document(doc_id):
    u=current_user(); conn=db(); d=conn.execute('SELECT * FROM documents WHERE id=? AND org_id=?',(doc_id,u['org_id'])).fetchone(); conn.close()
    if not d: return ('Not found',404)
    return send_from_directory(UPLOAD_DIR,d['filename'],as_attachment=False)

@app.route('/health')
def health(): return {'ok':True,'product':'JAXN Assist'}

if __name__=='__main__':
    init_db()
    app.run(host='0.0.0.0',port=int(os.getenv('PORT','8000')),debug=os.getenv('FLASK_DEBUG')=='1')
else:
    init_db()
