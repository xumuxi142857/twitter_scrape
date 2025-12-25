import time
import json
import random
import datetime
import sys
import itertools
from DrissionPage import ChromiumPage, ChromiumOptions


class TwitterProfileScraper:
    def __init__(self):
        co = ChromiumOptions()
        co.set_proxy("http://127.0.0.1:7897")
        self.page = ChromiumPage(co)
        self.page.listen.start('UserTweets')
        # 加载动画迭代器
        self.spinner = itertools.cycle(['|', '/', '-', '\\'])

    def _print_progress(self, current, total, start_time, status_msg="运行中..."):
        """
        UI 交互逻辑：心跳动画 + 进度条 + 状态栏
        """
        percent = 100 * (current / float(total)) if total > 0 else 0
        bar_length = 25
        filled_length = int(bar_length * current // total) if total > 0 else 0
        bar = '█' * filled_length + '░' * (bar_length - filled_length)

        elapsed_time = time.time() - start_time
        speed = current / elapsed_time if elapsed_time > 0.1 else 0

        spin_char = next(self.spinner)

        msg = (
            f"\r{spin_char} [{bar}] {percent:5.1f}% | "
            f"已存: {current}/{total} | "
            f"速度: {speed:4.1f}条/s | "
            f"状态: {status_msg}"
        )
        sys.stdout.write(msg.ljust(100))
        sys.stdout.flush()

    def scrape(self, username, total_target=100):
        target_url = f"https://x.com/{username}"

        print(f"{'=' * 70}")
        print(f"Twitter 用户主页监控启动 (UI优化版)")
        print(f"目标用户: @{username}")
        print(f"目标数量: {total_target}")
        print(f"{'=' * 70}")

        self.page.get(target_url)

        # 初始等待，为了用户体验，显示倒计时
        for i in range(5, 0, -1):
            sys.stdout.write(f"\r[-] 正在检查登录状态，请确保已登录... {i}s")
            sys.stdout.flush()
            time.sleep(1)

        collected_tweets = []
        start_time = time.time()

        # 初始 UI
        self._print_progress(0, total_target, start_time, "准备就绪")

        retry_count = 0

        while len(collected_tweets) < total_target:
            # 1. 滚动
            self._print_progress(len(collected_tweets), total_target, start_time, "正在滚动页面...")
            scroll_px = random.randint(600, 900)
            self.page.scroll.down(scroll_px)

            # 2. 监听数据包 (拆分为多次短等待，保持UI刷新)
            res = None
            for _ in range(5):  # 5秒超时
                res = self.page.listen.wait(timeout=1)
                if res: break
                self._print_progress(len(collected_tweets), total_target, start_time, "等待数据包...")

            if res:
                retry_count = 0
                try:
                    self._print_progress(len(collected_tweets), total_target, start_time, "正在解析数据...")
                    data = res.response.body
                    new_tweets = self._parse_profile_data(data)

                    if new_tweets:
                        existing_ids = set(t['tweet_id'] for t in collected_tweets)
                        unique_new = [t for t in new_tweets if t['tweet_id'] and t['tweet_id'] not in existing_ids]

                        if unique_new:
                            collected_tweets.extend(unique_new)
                            if len(collected_tweets) > total_target:
                                collected_tweets = collected_tweets[:total_target]
                            self._print_progress(len(collected_tweets), total_target, start_time,
                                                 f"新增 {len(unique_new)} 条")
                        else:
                            self._print_progress(len(collected_tweets), total_target, start_time, "去重中(无新增)...")
                    else:
                        # 可能是翻页包但不包含推文
                        pass
                except Exception:
                    pass
            else:
                retry_count += 1
                if retry_count > 3:
                    self._print_progress(len(collected_tweets), total_target, start_time,
                                         f"加载缓慢, 深度滚动({retry_count})...")
                    self.page.scroll.down(1000)
                    time.sleep(2)
                else:
                    self._print_progress(len(collected_tweets), total_target, start_time, "加载中...")

            # 3. 随机休眠 (保持动画)
            sleep_time = random.uniform(1.5, 3)
            steps = int(sleep_time / 0.2)
            for _ in range(steps):
                time.sleep(0.2)
                self._print_progress(len(collected_tweets), total_target, start_time, "模拟浏览停顿...")

            if len(collected_tweets) >= total_target:
                break

        print(f"\n{'-' * 70}")
        print(f"[+] 抓取完成! 总耗时: {time.time() - start_time:.2f}s")
        return collected_tweets[:total_target]

    def _parse_profile_data(self, data):
        """解析主页 UserTweets 接口的数据 (包含 v2/v3 修复逻辑)"""
        tweets = []
        try:
            if isinstance(data, str): data = json.loads(data)

            user_res = data.get('data', {}).get('user', {}).get('result', {})

            # 兼容 Timeline V2 和 普通 Timeline
            timeline_root = user_res.get('timeline_v2', {})
            if not timeline_root:
                timeline_root = user_res.get('timeline', {})

            timeline = timeline_root.get('timeline', {})
            instructions = timeline.get('instructions', [])

            for instr in instructions:
                entries = []
                if instr.get('type') == 'TimelineAddEntries':
                    entries = instr.get('entries', [])
                elif instr.get('type') == 'TimelinePinEntry':
                    entry = instr.get('entry')
                    if entry: entries = [entry]

                for entry in entries:
                    if entry.get('entryId', '').startswith('tweet-'):
                        t = self._extract_tweet_content(entry)
                        if t: tweets.append(t)
        except Exception:
            pass
        return tweets

    def _extract_tweet_content(self, entry):
        """提取推文内容 (包含 Unknown 用户名和 Retweet 点赞修复)"""
        try:
            item_content = entry.get('content', {}).get('itemContent', {})
            result = item_content.get('tweet_results', {}).get('result', {})

            if 'tweet' in result: result = result['tweet']

            # 1. 修复 Username Unknown
            user_core = result.get('core', {}).get('user_results', {}).get('result', {})
            username = "Unknown"
            if 'legacy' in user_core and 'screen_name' in user_core['legacy']:
                username = user_core['legacy']['screen_name']
            elif 'core' in user_core and 'screen_name' in user_core['core']:
                username = user_core['core']['screen_name']

            # 2. 修复 Retweet 点赞为 0
            tweet_legacy = result.get('legacy')
            if not tweet_legacy: return None

            full_text = tweet_legacy.get('full_text', '')
            is_retweet = 'retweeted_status_result' in tweet_legacy or full_text.startswith('RT @')

            stats_source = tweet_legacy
            if is_retweet and 'retweeted_status_result' in tweet_legacy:
                try:
                    stats_source = tweet_legacy['retweeted_status_result']['result']['legacy']
                except:
                    pass

            return {
                "scraped_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "tweet_id": tweet_legacy.get('id_str'),
                "created_at": tweet_legacy.get('created_at'),
                "username": username,
                "full_text": full_text,
                "lang": tweet_legacy.get('lang'),
                "is_retweet": is_retweet,
                "is_reply": True if tweet_legacy.get('in_reply_to_status_id_str') else False,
                "favorite_count": stats_source.get('favorite_count', 0),
                "retweet_count": stats_source.get('retweet_count', 0),
                "reply_count": stats_source.get('reply_count', 0),
                "quote_count": stats_source.get('quote_count', 0),
                "media_urls": [m['media_url_https'] for m in tweet_legacy.get('entities', {}).get('media', []) if
                               'media_url_https' in m]
            }
        except Exception:
            return None


if __name__ == "__main__":
    # 配置区
    TARGET_USERNAME = "ElonMusk"  # 不带@
    TASK_NAME = TARGET_USERNAME
    TARGET_COUNT = 10

    scraper = TwitterProfileScraper()
    data = scraper.scrape(TARGET_USERNAME, total_target=TARGET_COUNT)

    if data:
        # 生成带时间戳的文件名
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"search_{TASK_NAME}_{timestamp}.json"

        print(f"[预览第一条]: {json.dumps(data[0], indent=4, ensure_ascii=False)}")

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print(f"[OK] 文件已保存: {filename}")
    else:
        print("\n[Error] 未抓取到数据")