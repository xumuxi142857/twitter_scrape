import time
import json
import random
import datetime
import sys
import itertools
from DrissionPage import ChromiumPage, ChromiumOptions


class TwitterHybridScraper:
    def __init__(self):
        co = ChromiumOptions()
        co.set_proxy("http://127.0.0.1:7897")
        self.page = ChromiumPage(co)

        self.page.listen.start('SearchTimeline')
        self.spinner = itertools.cycle(['|', '/', '-', '\\'])

    def _print_progress(self, current, total, start_time, status_msg="运行中..."):

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
        sys.stdout.write(msg.ljust(110))
        sys.stdout.flush()

    def scrape(self, keyword, total_target=100):
        # 前一个是TOP，后一个是Lastest
        search_url = f"https://x.com/search?q={keyword}&src=typed_query"
        # search_url = f"https://x.com/search?q={keyword}&src=typed_query&f=live"

        print(f"{'=' * 70}")
        print(f"Twitter 关键词监控启动 (完整字段+强力滚动版)")
        print(f"搜索指令: {keyword}")
        print(f"目标数量: {total_target}")
        print(f"{'=' * 70}")

        self.page.get(search_url)

        collected_tweets = []
        seen_ids = set()  # 用于去重
        start_time = time.time()

        self._print_progress(0, total_target, start_time, "等待页面加载...")
        time.sleep(3)

        # === 核心控制变量 ===
        no_new_data_count = 0
        max_no_data_limit = 10  # 容忍 10 次无数据（应对广告区）

        scroll_unchanged_count = 0
        max_scroll_unchanged = 5  # 容忍 5 次高度不变

        last_height = self.page.run_js("return document.body.scrollHeight")

        while len(collected_tweets) < total_target:
            # 1. 滚动逻辑：智能判断是否需要“回拉”
            if no_new_data_count > 2:
                # 如果连续几次没数据，可能是卡在某个懒加载点了，执行大幅度动作
                self._print_progress(len(collected_tweets), total_target, start_time, "数据停滞，尝试大幅滚动...")
                self.page.scroll.to_bottom()
                time.sleep(1)
                self.page.scroll.up(300)  # 往回拉一点，触发加载
            else:
                self._print_progress(len(collected_tweets), total_target, start_time, "滚动加载中...")
                self.page.scroll.down(random.randint(600, 900))

            # 2. 等待数据包：放宽超时时间
            res = self.page.listen.wait(timeout=2.5)

            new_data_found = False

            if res:
                try:
                    self._print_progress(len(collected_tweets), total_target, start_time, "正在解析数据...")
                    data = res.response.body
                    parsed_tweets = self._parse_graphql_data(data)

                    if parsed_tweets:
                        unique_new = []
                        for t in parsed_tweets:
                            if t['tweet_id'] not in seen_ids:
                                seen_ids.add(t['tweet_id'])
                                unique_new.append(t)

                        if unique_new:
                            collected_tweets.extend(unique_new)
                            new_data_found = True

                            # 重置所有错误计数器
                            no_new_data_count = 0
                            scroll_unchanged_count = 0

                            self._print_progress(len(collected_tweets), total_target, start_time,
                                                 f"新增 {len(unique_new)} 条")
                except Exception:
                    pass

            # 3. 状态检查与熔断
            if not new_data_found:
                no_new_data_count += 1
                self._print_progress(len(collected_tweets), total_target, start_time,
                                     f"未发现新推文 ({no_new_data_count}/{max_no_data_limit})")

            # 4. 高度检查（辅助判断是否到底）
            curr_height = self.page.run_js("return document.body.scrollHeight")
            if curr_height == last_height:
                scroll_unchanged_count += 1
            else:
                scroll_unchanged_count = 0
                last_height = curr_height

            # 5. 退出条件判断
            if no_new_data_count >= max_no_data_limit:
                # 连续很多次都没数据，认为到底或被限制
                print("\n[Warning] 连续多次未获取新数据，停止抓取。")
                break

            if scroll_unchanged_count >= max_scroll_unchanged:
                # 高度一直不变，且尝试了滚动
                print("\n[Warning] 页面高度不再变化，判定为到达底部。")
                break

            # 6. 随机休眠防封
            time.sleep(random.uniform(1.2, 2.5))

        # 结束
        print(f"\n{'-' * 70}")
        print(f"[+] 抓取完成! 总耗时: {time.time() - start_time:.2f}s")
        # 再次截断，确保不超过目标数量太多
        return collected_tweets[:total_target]

    def _parse_graphql_data(self, data):
        tweets = []
        try:
            if isinstance(data, str): data = json.loads(data)

            # 尝试定位 timeline，根据不同的接口结构可能略有不同，这里做泛化处理
            instructions = []

            # 路径A: search_by_raw_query (SearchTimeline)
            if 'data' in data and 'search_by_raw_query' in data['data']:
                timeline = data['data']['search_by_raw_query']['search_timeline']['timeline']
                instructions = timeline.get('instructions', [])

            # 路径B: 备用路径
            elif 'timeline' in data:
                instructions = data['timeline'].get('instructions', [])

            for instr in instructions:
                entries = []
                if instr.get('type') == 'TimelineAddEntries':
                    entries = instr.get('entries', [])
                elif instr.get('type') == 'TimelineReplaceEntry':
                    if instr['entry']['entryId'].startswith('tweet-'):
                        entries = [instr['entry']]

                for entry in entries:
                    if entry.get('entryId', '').startswith('tweet-'):
                        t = self._extract_tweet_content(entry)
                        if t: tweets.append(t)
        except Exception:
            pass
        return tweets

    def _extract_tweet_content(self, entry):
        """
        完整提取字段，匹配用户要求的格式
        """
        try:
            item_content = entry.get('content', {}).get('itemContent', {})
            result = item_content.get('tweet_results', {}).get('result', {})

            if 'tweet' in result: result = result['tweet']
            tweet_legacy = result.get('legacy')
            if not tweet_legacy: return None

            # 提取用户名
            username = "Unknown"
            try:
                user_core = result.get('core', {}).get('user_results', {}).get('result', {})
                if 'legacy' in user_core and 'screen_name' in user_core['legacy']:
                    username = user_core['legacy']['screen_name']
                elif 'core' in user_core and 'screen_name' in user_core['core']:
                    username = user_core['core']['screen_name']
            except Exception:
                pass

            full_text = tweet_legacy.get('full_text', '')
            is_retweet = 'retweeted_status_result' in tweet_legacy or full_text.startswith('RT @')

            # 提取媒体链接
            media_urls = []
            if 'entities' in tweet_legacy and 'media' in tweet_legacy['entities']:
                for m in tweet_legacy['entities']['media']:
                    if 'media_url_https' in m: media_urls.append(m['media_url_https'])

            # 构造完整的返回对象
            return {
                "scraped_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "tweet_id": tweet_legacy.get('id_str'),
                "created_at": tweet_legacy.get('created_at'),
                "username": username,
                "full_text": full_text,
                "lang": tweet_legacy.get('lang'),
                "is_retweet": is_retweet,
                "is_reply": True if tweet_legacy.get('in_reply_to_status_id_str') else False,
                "favorite_count": tweet_legacy.get('favorite_count', 0),
                "retweet_count": tweet_legacy.get('retweet_count', 0),
                "reply_count": tweet_legacy.get('reply_count', 0),
                "quote_count": tweet_legacy.get('quote_count', 0),
                "media_urls": media_urls
            }
        except Exception:
            return None


if __name__ == "__main__":
    # 在这里更改搜索关键词，taskname用于修改文件名
    TARGET_KEYWORD = '(China OR Chinese) (US OR USA OR America) (relations OR trade OR policy OR tension OR deal)'
    TASK_NAME = "China_US_Relations"
    TARGET_COUNT = 400

    scraper = TwitterHybridScraper()
    # 只要 scrape 函数正常 return，这里就能拿到数据
    data = scraper.scrape(TARGET_KEYWORD, total_target=TARGET_COUNT)

    if data:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"search_{TASK_NAME}_{timestamp}.json"

        print(f"[预览第一条]: {json.dumps(data[0], indent=4, ensure_ascii=False)}")
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print(f"[OK] 文件已保存: {filename} (共 {len(data)} 条)")
    else:
        print("\n[Error] 未抓取到数据")