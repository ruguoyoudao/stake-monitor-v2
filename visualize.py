import streamlit as st
import json
import re
from datetime import datetime, timedelta, date
from pathlib import Path
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

DATA_FILE = Path(__file__).parent / 'large_bets.json'
CLUSTER_FILE = Path(__file__).parent / 'cluster_alerts.json'


@st.cache_data(ttl=30)
def load_data():
    if not DATA_FILE.exists():
        return []
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


@st.cache_data(ttl=30)
def load_clusters():
    if not CLUSTER_FILE.exists():
        return {}
    with open(CLUSTER_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def extract_currency(amount_str):
    if not amount_str:
        return 'Unknown'
    m = re.match(r'^([A-Z]+)', amount_str)
    return m.group(1) if m else 'Unknown'


def build_dataframe(bets):
    rows = []
    for b in bets:
        dt = None
        saved_at = b.get('saved_at', '')
        if saved_at:
            try:
                dt = datetime.fromisoformat(saved_at)
            except (ValueError, TypeError):
                pass
        rows.append({
            'event': b.get('event', ''),
            'player': b.get('player', ''),
            'time': b.get('time', ''),
            'odds': float(b.get('odds', 0)) if b.get('odds') else 0,
            'amount': b.get('amount', ''),
            'amount_cny': b.get('amount_cny', 0),
            'currency': extract_currency(b.get('amount', '')),
            'market': b.get('market', ''),
            'outcome': b.get('outcome', ''),
            'share_link': b.get('share_link', ''),
            'saved_at': dt,
        })
    df = pd.DataFrame(rows)
    if not df.empty and 'saved_at' in df.columns:
        df = df.sort_values('saved_at', ascending=False)
    return df


st.set_page_config(page_title='Stake Monitor', layout='wide')
st.title('Stake Monitor - \u5927\u989d\u6295\u6ce8\u770b\u677f')

bets = load_data()
if not bets:
    st.warning('\u6682\u65e0\u5927\u989d\u6295\u6ce8\u6570\u636e')
    st.stop()

df = build_dataframe(bets)

st.sidebar.header('\u7b5b\u9009\u6761\u4ef6')

min_cny = int(df['amount_cny'].min()) if not df.empty else 0
max_cny = int(df['amount_cny'].max()) if not df.empty else 0
cny_threshold = st.sidebar.slider('\u6700\u5c0f\u91d1\u989d (CNY)', min_value=min_cny, max_value=max_cny, value=min_cny, step=1000)

currencies = sorted(df['currency'].unique())
selected_currencies = st.sidebar.multiselect('\u5e01\u79cd', currencies, default=currencies)

odds_max = float(df['odds'].max()) if not df.empty else 10.0
odds_range = st.sidebar.slider('\u8d54\u7387\u8303\u56f4', min_value=0.0, max_value=odds_max,
                                value=(0.0, odds_max), step=0.1)

event_search = st.sidebar.text_input('\u8d5b\u4e8b\u641c\u7d22\uff08\u5173\u952e\u8bcd\uff09')

if 'saved_at' in df.columns and df['saved_at'].notna().any():
    min_dt = df['saved_at'].min()
    max_dt = df['saved_at'].max()
    today = date.today()
    date_range = st.sidebar.date_input('时间范围', value=(today, today),
                                        min_value=min_dt.date(), max_value=max_dt.date())
else:
    date_range = None

mask = df['amount_cny'] >= cny_threshold
mask &= df['currency'].isin(selected_currencies)
mask &= (df['odds'] >= odds_range[0]) & (df['odds'] <= odds_range[1])
if event_search:
    mask &= df['event'].str.contains(event_search, case=False, na=False)
if date_range and len(date_range) == 2:
    start_dt = pd.Timestamp(datetime.combine(date_range[0], datetime.min.time()))
    end_dt = pd.Timestamp(datetime.combine(date_range[1], datetime.max.time()))
    mask &= (df['saved_at'] >= start_dt) & (df['saved_at'] <= end_dt)

filtered = df[mask]

st.subheader('\u6982\u89c8\u6307\u6807')
total_bets = len(filtered)
total_cny = filtered['amount_cny'].sum()
avg_cny = filtered['amount_cny'].mean() if total_bets > 0 else 0

col1, col2, col3, col4 = st.columns(4)
col1.metric('\u6295\u6ce8\u7b14\u6570', total_bets)
col2.metric('\u603b\u91d1\u989d (CNY)', '{:,.0f}'.format(total_cny))
col3.metric('\u5e73\u5747\u91d1\u989d (CNY)', '{:,.0f}'.format(avg_cny))
if total_bets > 0:
    max_row = filtered.loc[filtered['amount_cny'].idxmax()]
    col4.metric('\u6700\u5927\u5355\u7b14', '{:,.0f} CNY'.format(max_row['amount_cny']))

st.subheader('\u56fe\u8868\u5206\u6790')

tab_names = ['\u91d1\u989d\u5206\u5e03', '\u8d54\u7387 vs \u91d1\u989d', '\u8d5b\u4e8b\u70ed\u5ea6', '\u8d8b\u52bf\u65f6\u95f4\u7ebf', '\u8d5b\u4e8b\u73a9\u6cd5\u5206\u6790']
tab1, tab2, tab3, tab4, tab5 = st.tabs(tab_names)

with tab1:
    fig_hist = px.histogram(
        filtered, x='amount_cny', nbins=30,
        title='\u6295\u6ce8\u91d1\u989d\u5206\u5e03 (CNY)',
        labels={'amount_cny': '\u91d1\u989d (CNY)'},
        color_discrete_sequence=['#1f77b4'],
    )
    st.plotly_chart(fig_hist, use_container_width=True)

with tab2:
    fig_scatter = px.scatter(
        filtered, x='odds', y='amount_cny',
        color='currency', hover_data=['event', 'player', 'outcome'],
        title='\u8d54\u7387 vs \u91d1\u989d',
        labels={'odds': '\u8d54\u7387', 'amount_cny': '\u91d1\u989d (CNY)'},
    )
    st.plotly_chart(fig_scatter, use_container_width=True)

with tab3:
    event_stats = (filtered.groupby('event').agg(
        total_cny=('amount_cny', 'sum'),
        count=('amount_cny', 'count'),
        avg_odds=('odds', 'mean'),
    ).sort_values('total_cny', ascending=False).head(20))

    fig_bar = px.bar(
        event_stats.reset_index(), x='total_cny', y='event', orientation='h',
        title='\u8d5b\u4e8b\u70ed\u5ea6 TOP 20 (\u6309\u603b\u91d1\u989d)',
        hover_data=['count', 'avg_odds'],
    )
    fig_bar.update_layout(yaxis={'categoryorder': 'total ascending'})
    st.plotly_chart(fig_bar, use_container_width=True)

with tab4:
    if filtered['saved_at'].notna().any():
        timeline = filtered.set_index('saved_at').resample('10min')['amount_cny'].sum().reset_index()
        timeline.columns = ['\u65f6\u95f4', '\u603b\u91d1\u989d']
        fig_timeline = px.area(timeline, x='\u65f6\u95f4', y='\u603b\u91d1\u989d', title='\u6295\u6ce8\u65f6\u95f4\u7ebf (10min)')
        st.plotly_chart(fig_timeline, use_container_width=True)
    else:
        st.info('\u65e0\u65f6\u95f4\u6570\u636e')

with tab5:
    if event_search:
        target_events = filtered[filtered['event'].str.contains(event_search, case=False, na=False)]
        st.info('\u8d5b\u4e8b\u641c\u7d22: [{}] \u5339\u914d {} \u6761'.format(event_search, len(target_events)))
    else:
        available_events = sorted(filtered['event'].unique())
        selected_event = st.selectbox('\u9009\u62e9\u8d5b\u4e8b', options=available_events)
        target_events = filtered[filtered['event'] == selected_event]

    if target_events.empty:
        st.info('\u65e0\u5339\u914d\u6570\u636e\uff0c\u8bf7\u5148\u9009\u62e9\u8d5b\u4e8b\u6216\u8f93\u5165\u5173\u952e\u8bcd')
    else:
        detail_stats = target_events.groupby(['market', 'outcome']).agg(
            count=('amount_cny', 'count'),
            total_cny=('amount_cny', 'sum'),
            avg_odds=('odds', 'mean'),
        ).sort_values('total_cny', ascending=False)

        st.write('**\u73a9\u6cd5-\u7ed3\u679c\u660e\u7ec6**')
        display_detail = detail_stats.reset_index()
        display_detail.columns = ['\u73a9\u6cd5', '\u7ed3\u679c', '\u7b14\u6570', '\u603b\u91d1\u989d(CNY)', '\u5e73\u5747\u8d54\u7387']
        st.dataframe(
            display_detail,
            use_container_width=True,
            column_config={
                '\u603b\u91d1\u989d(CNY)': st.column_config.NumberColumn(format='%,.0f'),
                '\u5e73\u5747\u8d54\u7387': st.column_config.NumberColumn(format='%.2f'),
            },
            hide_index=True,
        )

        plot_df = target_events.copy()
        plot_df['outcome'] = plot_df['outcome'].fillna('(\u7a7a)')
        plot_df['market'] = plot_df['market'].fillna('(\u7a7a)')
        fig_detail = px.bar(
            plot_df.sort_values('outcome'), x='outcome', y='amount_cny',
            color='market', barmode='group',
            title='\u73a9\u6cd5-\u7ed3\u679c\u5206\u7ec4\u6761\u5f62\u56fe',
            labels={'amount_cny': '\u91d1\u989d (CNY)', 'outcome': '\u7ed3\u679c', 'market': '\u73a9\u6cd5'},
            hover_data=['player', 'odds', 'currency'],
        )
        st.plotly_chart(fig_detail, use_container_width=True)

st.subheader('\u805a\u7c7b\u68c0\u6d4b\u7ed3\u679c')
clusters = load_clusters()
if clusters:
    cluster_data = []
    for key, count in clusters.items():
        parts = key.split('|')
        cluster_data.append({
            '\u8d5b\u4e8b': parts[0] if len(parts) > 0 else '',
            '\u73a9\u6cd5': parts[1] if len(parts) > 1 else '',
            '\u7ed3\u679c': parts[2] if len(parts) > 2 else '',
            '\u5df2\u901a\u77e5\u6b21\u6570': count,
        })
    st.dataframe(pd.DataFrame(cluster_data), use_container_width=True)
else:
    st.info('\u6682\u65e0\u805a\u7c7b\u544a\u8b66\u8bb0\u5f55')

st.subheader('\u6295\u6ce8\u660e\u7ec6')
display_cols = ['saved_at', 'event', 'market', 'outcome', 'player', 'odds', 'amount', 'amount_cny', 'currency']
display_df = filtered[display_cols].copy()
display_df.columns = ['\u65f6\u95f4', '\u8d5b\u4e8b', '\u73a9\u6cd5', '\u7ed3\u679c', '\u73a9\u5bb6', '\u8d54\u7387', '\u539f\u59cb\u91d1\u989d', 'CNY\u91d1\u989d', '\u5e01\u79cd']
display_df['\u65f6\u95f4'] = display_df['\u65f6\u95f4'].dt.strftime('%m-%d %H:%M')

st.dataframe(
    display_df,
    use_container_width=True,
    column_config={
        'CNY\u91d1\u989d': st.column_config.NumberColumn(format='%,.0f'),
        '\u8d54\u7387': st.column_config.NumberColumn(format='%.2f'),
    },
    hide_index=True,
)
