from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import hmac
import json
import logging
import math
import os
import secrets
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

# 気象庁防災情報JSONの青森市の地域コード
AREA_CODE = "0220100"

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json_atomic(path, data):
    directory = os.path.dirname(path) or '.'
    descriptor, temporary_path = tempfile.mkstemp(prefix='.data-', dir=directory)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])

def save_instructions(updated_instructions=None):
    """指示ボードのデータをファイルに保存する"""
    records = instructions if updated_instructions is None else updated_instructions
    save_json_atomic(INSTRUCTIONS_FILE, records)
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


@app.context_processor
def provide_csrf_token():
    token = session.get('csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['csrf_token'] = token
    page_titles = {
        'index': 'ホーム',
        'login': 'ログイン',
        'shelter_register': '避難所登録',
        'shelter_search': '避難所検索',
        'all_shelters': '避難所一覧',
        'search_results': '検索結果',
        'board': '指示ボード',
    }
    return {
        'csrf_token': token,
        'current_endpoint': request.endpoint,
        'page_title': page_titles.get(request.endpoint, '防災アプリ'),
    }


def csrf_token_is_valid():
    token = session.get('csrf_token', '')
    submitted_token = request.form.get('csrf_token', '')
    return bool(token and submitted_token and hmac.compare_digest(token, submitted_token))

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    return [s for s in shelters if not district or s.get('district') == district]


def parse_coordinates(latitude, longitude):
    """Return validated coordinates, or None when both values are omitted."""
    latitude = str(latitude or '').strip()
    longitude = str(longitude or '').strip()
    if not latitude and not longitude:
        return None, None
    if not latitude or not longitude:
        raise ValueError('緯度と経度は両方入力するか、両方省略してください。')
    try:
        latitude_value = float(latitude)
        longitude_value = float(longitude)
    except ValueError as error:
        raise ValueError('緯度と経度は数値で入力してください。') from error
    if not math.isfinite(latitude_value) or not math.isfinite(longitude_value):
        raise ValueError('緯度と経度には有限の数値を入力してください。')
    if not -90 <= latitude_value <= 90 or not -180 <= longitude_value <= 180:
        raise ValueError('緯度は-90〜90、経度は-180〜180の範囲で入力してください。')
    return latitude_value, longitude_value


def shelter_coordinates(shelter):
    try:
        return parse_coordinates(
            shelter.get('latitude', shelter.get('lat')),
            shelter.get('longitude', shelter.get('lng')),
        )
    except ValueError:
        return None, None


def normalized_opening_status(shelter):
    return '開設中' if shelter.get('opening_status') == '開設中' else '未開設'


def instruction_time_key(instruction):
    value = instruction.get('created_at_iso') or instruction.get('created_at', '')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=JST)
        return parsed.timestamp()
    except (ValueError, AttributeError):
        try:
            parsed = datetime.strptime(value, '%Y年%m月%d日 %H:%M').replace(tzinfo=JST)
            return parsed.timestamp()
        except (ValueError, TypeError):
            return 0


def instruction_status(instruction):
    status = instruction.get('status')
    if status == '発信中':
        status = '対応中'
    if status not in ('対応中', '解除', '完了'):
        return '対応中'
    audience = instruction_audience(instruction)
    if (audience == 'resident' and status == '完了') or (audience == 'staff' and status == '解除'):
        return '対応中'
    return status


def instruction_audience(instruction):
    audience = instruction.get('audience')
    if audience in ('resident', 'staff'):
        return audience
    target = instruction.get('target', '住民')
    return 'resident' if target in ('住民', '観光客', '観光客向け', '住民・観光客') else 'staff'


def board_context():
    ordered = sorted(instructions, key=instruction_time_key, reverse=True)
    view_records = []
    for instruction in ordered:
        record = dict(instruction)
        record['_display_status'] = instruction_status(instruction)
        record['_audience'] = instruction_audience(instruction)
        view_records.append(record)
    return {
        'active_instructions': [item for item in view_records if item['_display_status'] == '対応中'],
        'resolved_instructions': [item for item in view_records if item['_display_status'] in ('完了', '解除')],
        'shelters': shelters,
    }


def render_board(error=None, status_code=200):
    return render_template('board.html', error=error, **board_context()), status_code


def public_instruction(instruction):
    return instruction.get('target') in ('住民', '観光客', '観光客向け', '住民・観光客')


def instruction_for_api(instruction):
    return {
        'id': instruction.get('id'),
        'target': instruction.get('target', '住民'),
        'content': instruction.get('content', ''),
        'urgency': instruction.get('urgency', '通常'),
        'shelter': instruction.get('shelter', ''),
        'status': instruction_status(instruction),
        'created_at': instruction.get('created_at', '日時不明'),
        'created_at_iso': instruction.get('created_at_iso', ''),
        'updated_at': instruction.get('updated_at', instruction.get('created_at', '日時不明')),
    }


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    latest_status_by_code = {}
    report_datetimes = []

    def report_time_key(report):
        value = report.get('reportDatetime', '') if isinstance(report, dict) else ''
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except (ValueError, AttributeError):
            return 0

    for report in sorted(warning_data, key=report_time_key):
        if not isinstance(report, dict):
            continue

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if not area:
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status == '発表警報・注意報はなし':
                latest_status_by_code.clear()
                continue
            if not code or not status:
                continue

            latest_status_by_code[code] = {
                'status': status,
                'report_datetime': report_datetime or '',
            }

    warnings = [
        {
            'name': WARNING_CODES.get(code, f"不明な警報・注意報 (コード: {code})"),
            'code': code,
            'status': details['status'],
        }
        for code, details in latest_status_by_code.items()
        if details['status'] in ('発表', '継続')
    ]

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception as error:
        logging.exception('気象情報の取得に失敗しました')
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "error": True,
            "message": str(error)
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    return render_template('index.html')

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    if request.method == 'POST':
        if not csrf_token_is_valid():
            return render_template(
                'shelter_register.html', error=True,
                message='送信を確認できませんでした。画面を再読み込みしてください。'
            ), 400
        name = request.form.get('name', '').strip()
        address = request.form.get('address', '').strip()
        if not name or len(name) > 100:
            return render_template(
                'shelter_register.html', error=True,
                message='避難所名を入力してください（100文字以内）。'
            )
        if len(address) > 200:
            return render_template(
                'shelter_register.html', error=True,
                message='住所は200文字以内で入力してください。'
            )

        if any(shelter.get('name') == name for shelter in shelters):
            return render_template(
                'shelter_register.html', error=True,
                message='この避難所名はすでに登録されています。'
            )

        try:
            latitude, longitude = parse_coordinates(
                request.form.get('latitude'), request.form.get('longitude')
            )
        except ValueError as error:
            return render_template(
                'shelter_register.html', error=True, message=str(error)
            )

        opening_status = request.form.get('opening_status', '')
        if opening_status not in ('開設中', '未開設'):
            opening_status = '未開設'
        new_shelter = {
            'id': max((shelter.get('id', 0) for shelter in shelters), default=0) + 1,
            'name': name,
            'address': address,
            'opening_status': opening_status,
        }
        if latitude is not None:
            new_shelter['latitude'] = latitude
            new_shelter['longitude'] = longitude
        updated_shelters = [*shelters, new_shelter]
        try:
            save_json_atomic(DATA_FILE, updated_shelters)
        except OSError:
            logging.exception('避難所の保存に失敗しました')
            return render_template(
                'shelter_register.html', error=True,
                message='避難所を保存できませんでした。'
            ), 500

        shelters[:] = updated_shelters
        return render_template(
            'shelter_register.html', success=True,
            message='避難所を登録しました。'
        )

    return render_template('shelter_register.html')

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    return render_template('shelter_search.html')

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template('search_results.html', results=shelters)


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board', methods=['GET', 'POST'])
@login_required
def board():
    if request.method == 'POST':
        if not csrf_token_is_valid():
            return render_board('送信を確認できませんでした。画面を再読み込みしてください。', 400)

        selected_target = request.form.get('target', '')
        custom_target = request.form.get('custom_target', '').strip()
        if selected_target == '__custom__':
            target = custom_target
            audience = 'staff'
        else:
            target = selected_target
            audience = 'resident' if target == '住民' else 'staff'
        content = request.form.get('content', '').strip()
        urgency = request.form.get('urgency', '通常')
        shelter_name = request.form.get('shelter', '').strip()
        custom_place = request.form.get('custom_place', '').strip()
        if shelter_name == '__custom__':
            shelter_name = custom_place
        if (selected_target == '__custom__' and not target) or (
            selected_target != '__custom__'
            and target not in ('住民', '観光客', '住民・観光客', '防災課', '道路管理課', '職員')
        ):
            message = '宛先を選択してください。'
        elif not content or len(content) > 2000:
            message = '内容を入力してください（2000文字以内）。'
        elif len(target) > 80 or len(shelter_name) > 100:
            message = '宛先または避難先の入力が長すぎます。'
        elif selected_target != '__custom__' and custom_target:
            message = '自由入力の宛先を使う場合は「その他」を選択してください。'
        elif request.form.get('shelter') == '__custom__' and not shelter_name:
            message = '避難先・場所を入力してください。'
        elif urgency not in ('通常', '高・緊急'):
            message = '緊急度を選び直してください。'
        elif request.form.get('shelter') not in ('', '__custom__') and not any(item.get('name') == shelter_name for item in shelters):
            message = '登録済みの避難所を選択してください。'
        else:
            message = None

        if message:
            return render_board(message, 400)

        now = datetime.now(JST)
        new_instruction = {
            'id': max((item.get('id', 0) for item in instructions), default=0) + 1,
            'target': target,
            'audience': audience,
            'content': content,
            'urgency': urgency,
            'shelter': shelter_name,
            'status': '対応中',
            'created_at': now.strftime('%Y年%m月%d日 %H:%M'),
            'created_at_iso': now.isoformat(timespec='seconds'),
            'updated_at': now.strftime('%Y年%m月%d日 %H:%M'),
        }
        updated_instructions = [*instructions, new_instruction]
        try:
            save_instructions(updated_instructions)
        except (OSError, TypeError, ValueError):
            logging.exception('指示の保存に失敗しました')
            return render_board('指示を保存できませんでした。時間をおいて再度お試しください。', 500)
        instructions[:] = updated_instructions
        return redirect(url_for('board', updated='1') + '#active-instructions')

    return render_board()


@app.route('/board/<int:instruction_id>/status', methods=['POST'])
@login_required
def update_instruction_status(instruction_id):
    if not csrf_token_is_valid():
        return render_board('送信を確認できませんでした。画面を再読み込みしてください。', 400)

    updated_instructions = [dict(item) for item in instructions]
    target_instruction = next(
        (item for item in updated_instructions if item.get('id') == instruction_id), None
    )
    if target_instruction is None:
        return render_board('対象の指示が見つかりません。', 404)

    action = request.form.get('action', '')
    expected_status = request.form.get('expected_status', '')
    current_status = instruction_status(target_instruction)
    is_resident = instruction_audience(target_instruction) == 'resident'
    transitions = {
        'release': ('対応中', '解除', True),
        'complete': ('対応中', '完了', False),
        'undo_release': ('解除', '対応中', True),
        'undo_complete': ('完了', '対応中', False),
    }
    transition = transitions.get(action)
    if not transition:
        return render_board('無効な状態変更操作です。', 400)
    from_status, to_status, resident_only = transition
    if expected_status != from_status or current_status != from_status:
        return render_board('指示の状態が更新されています。画面を再読み込みしてください。', 409)
    if is_resident != resident_only:
        return render_board('この対象には許可されていない操作です。', 400)

    target_instruction['status'] = to_status
    target_instruction['updated_at'] = get_japan_time()
    target_instruction['updated_at_iso'] = datetime.now(JST).isoformat(timespec='seconds')
    try:
        save_instructions(updated_instructions)
    except (OSError, TypeError, ValueError):
        logging.exception('指示状態の保存に失敗しました')
        return render_board('指示状態を保存できませんでした。状態は変更されていません。', 500)
    instructions[:] = updated_instructions
    anchor = 'resolved-instructions' if to_status in ('完了', '解除') else 'active-instructions'
    return redirect(url_for('board', updated='1') + f'#{anchor}')

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    results = filter_shelters(request.args.get('district'))
    return render_template('search_results.html', results=results)


@app.route('/api/shelters/map')
def api_shelters_map():
    mapped_shelters = []
    location_unregistered_count = 0
    for shelter in shelters:
        latitude, longitude = shelter_coordinates(shelter)
        if latitude is None:
            location_unregistered_count += 1
            continue
        mapped_shelters.append({
            'id': shelter.get('id'),
            'name': shelter.get('name') or '名称未登録',
            'address': shelter.get('address') or '未登録',
            'opening_status': normalized_opening_status(shelter),
            'latitude': latitude,
            'longitude': longitude,
        })
    return jsonify({
        'shelters': mapped_shelters,
        'location_unregistered_count': location_unregistered_count,
    })


@app.route('/api/public_instructions')
def api_public_instructions():
    public_records = [instruction for instruction in instructions if public_instruction(instruction)]
    public_records.sort(key=instruction_time_key, reverse=True)
    return jsonify({
        'instructions': [instruction_for_api(item) for item in public_records],
        'fetched_at': get_japan_time(),
    })

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)
