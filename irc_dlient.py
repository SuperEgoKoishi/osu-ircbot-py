import irc.client
import re
import requests

import threading
import chardet
import configparser

from requests.exceptions import HTTPError

from datetime import datetime, timedelta

import rosu_pp_py as rosu

import os
import json
import time

osu_server = "irc.ppy.sh"
osu_port = 6667


class Config:
    def __init__(self):
        self.config = configparser.ConfigParser()
        with open('config.ini', 'rb') as f:
            encoding = chardet.detect(f.read())['encoding']
        self.config.read('config.ini', encoding=encoding)
        self.osuclientid = self.config['OSUAPI']['client_id']
        self.osuclientsecret = self.config['OSUAPI']['client_secret']
        self.osunickname = self.config['OSUAPI']['nickname']
        self.osupassword = self.config['OSUAPI']['password']
        self.mpname = self.config['OSU']['mpname']
        self.starlimit = self.config['OSU']['starlimit']
        self.timelimit = self.config['OSU']['timelimit']
        self.mppassword = self.config['OSU']['mppassword']
        self.predict_url = self.config.get('PREDICT', 'url', fallback='')


# 定义IRC客户端类
class MyIRCClient:
    def __init__(self, server, port, config, p, r, b, pp):
        self.irc_react = irc.client.Reactor()
        self.config = config
        self.server = self.irc_react.server()
        self.server.connect(server, port, config.osunickname, config.osupassword)
        self.irc_react.add_global_handler("welcome", self.on_connect)
        self.irc_react.add_global_handler("pubmsg", self.on_pubmsg)
        self.irc_react.add_global_handler("privmsg", self.on_privmsg)
        self.timer = None  # 定义定时器
        self.restarting_task = threading.Thread(target=(self.restart))
        self.p = p
        self.r = r
        self.b = b
        self.pp = pp
        self.connection = None
        self.event = None
        self.has_connected = threading.Event()
        self.reactor_stoped = threading.Event()
        self.reactor_task = threading.Thread(target=self.process_forever)
        self.sender_task = threading.Thread(target=(self.send_loop))

    def start(self):
        self.reactor_task.start()
        print("事件循环线程启动")
        self.has_connected.wait()
        self.sender_task.start()
        print("发送线程启动")
        self.reactor_task.join()
        print("事件循环线程结束")
        self.sender_task.join()
        print("发送线程结束")

    def process_forever(self):
        try:
            while not self.reactor_stoped.is_set():
                self.irc_react.process_once(timeout=0.2)
        except Exception as e:
            print(f"事件循环线程发生错误: {e}")

    def reset_all(self):
        # 重置
        self.p.reset_player_list()
        self.p.reset_host_list()
        self.p.clear_approved_list()
        self.b.clear_cache()

    def restart(self):
        print(f'尝试重启...{datetime.now()+timedelta(hours=8)}')
        time.sleep(120)
        self.reset_all()
        self.r.create_room(self.server, "")
        self.restarting_task = threading.Thread(target=(self.restart))
        print(f'重启完成{datetime.now()+timedelta(hours=8)}')

    # 定义定时任务,每60s执行一次,检查房间状态
    def start_periodic_task(self):
        # Save the Timer object in an instance variable
        self.timer = threading.Timer(60, self.start_periodic_task)
        self.timer.start()
        self.check_room_status(self.r.room_id)

    # 停止定时任务
    def stop_periodic_task(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def check_last_room_status(self,last_room_id):
        if last_room_id == "":
            return False
        try:
            self.b.get_token()
            text = self.b.get_match_info(re.findall(r'\d+', last_room_id)[0])
            # match-disbanded #比赛关闭
            if "match-disbanded" in str(text['events']):
                return False
            else:
                self.r.change_room_id(last_room_id)
                return True
        except:
            print("获取上一个房间失败")
            return False
        
    # 这是检查房间状态的流程 用于定时任务
    def check_room_status(self, room_id):
        self.b.get_token()
        try:
            text = self.b.get_match_info(re.findall(r'\d+', room_id)[0])
        except:
            text = ""
        # match-disbanded #比赛关闭
        try:
            if ("match-disbanded" in str(text['events'])) == True:
                self.stop_periodic_task()
                # 重置
                p.reset_player_list()
                self.p.reset_host_list()
                self.p.clear_approved_list()
                self.p.approved_host_rotate_list.clear()
                self.b.clear_cache()
                # 尝试重新创建房间
                try:
                    self.r.create_room(self.server, "")
                except:
                    print("创建房间失败")
                    self.timer.start()
        except:
            print("无法判断比赛信息")

    def export_json(self):
        result = {}
        result['player_list'] = self.p.player_list
        result['beatmap_name'] = self.b.beatmap_name
        result['beatmap_artist'] = self.b.beatmap_artist
        result['beatmap_star'] = self.b.beatmap_star

        try:
            with open('data.json', 'w', encoding='utf-8') as f:
                json.dump(result, f)
            print("导出json")
        except:
            print("导出json失败")

    def on_connect(self, connection, event):
        last_room_id = self.r.get_last_room_id()
        
        # 如果房间存在
        if self.check_last_room_status(last_room_id):
            self.r.join_room(connection, event)
            self.r.change_password(connection, event)
            self.r.get_mp_settings(connection, event)
            try:
                self.start_periodic_task()
            except:
                print("定时任务启动失败")
        # 如果房间不存在
        else:
            self.r.create_room(connection, event)
        self.connection = connection
        self.event = event
        self.has_connected.set()

    def send_loop(self):
        while True:
            message = input()
            if message == "stop":
                self.stop()
                break
            self.r.send_msg(self.connection, self.event, message)

    def stop(self):
        print("IRC客户端已响应停止")
        if self.timer:
            self.stop_periodic_task()
        print("断开所有连接")
        self.irc_react.disconnect_all()
        print("处理最后一个事件")
        self.irc_react.process_once(timeout=0.2)
        self.reactor_stoped.set()

    def on_privmsg(self, connection, event):
        # 打印接收到的私人消息
        print(f"收到私人消息  {event.source.split('!')[0]}:{event.arguments[0]}")
        text = event.arguments[0]
        # 匹配第一次收到的房间号
        if text.find("Created the tournament match") != -1 and event.source.find("BanchoBot") != -1:
            try:
                romm_id = "#mp_"+re.findall(r'\d+', text)[0]
            except:
                romm_id = ""
            # 更新room变量
            self.r.change_room_id(romm_id)
            # 加入并监听房间
            self.r.join_room(connection, event)
            # 修改房间密码
            self.r.change_password(connection, event)
            # 保存房间IDs
            self.r.save_last_room_id()
            # 启动定时任务
            self.start_periodic_task()

    def on_pubmsg(self, connection, event):

        try:
            # 打印接收到的消息
            print(event.source)
            print(f"收到频道消息  {event.source.split('!')[0]}:{event.arguments[0]}")
            text = event.arguments[0]
            # 判断是否是banchobot发送的消息
            if event.source.find("BanchoBot!") != -1 or event.source.find("ATRI1024!") != -1:
                # 加入房间
                if text.find("joined in slot") != -1:
                    # 尝试
                    try:
                        playerid = re.findall(
                            r'.*(?= joined in slot)', text)[0]
                    except:
                        playerid = ""
                    print(f'玩家{playerid}加入房间')
                    # 发送欢迎消息
                    if "ATRI1024" not in playerid:
                        if self.b.beatmap_length != "" and self.r.game_start_time != "":
                            timeleft = int(self.b.beatmap_length)+10 - \
                                int((datetime.now()-self.r.game_start_time).seconds)
                            text_timeleft = f'| 剩余游玩时间：{timeleft}s 请主人耐心等待哦~'
                        else:
                            timeleft = 0
                        text_Welcome = f'欢迎{playerid}酱~＼(≧▽≦)／ 输入help获取指令详情'
                        if timeleft > 0:
                            self.r.send_msg(connection, event,
                                       text_Welcome+text_timeleft)
                        else:
                            self.r.send_msg(connection, event, text_Welcome)
                    # 如果第一次加入房间，更换房主，清空房主队列，设置FM
                    if len(self.p.player_list) == 0:
                        self.p.reset_host_list()
                        self.r.change_host(connection, event, playerid)
                        self.r.change_mods_to_FM(connection, event)
                    # 加入房间队列，玩家队列
                    self.p.add_host(playerid)
                    self.p.add_player(playerid)
                    print(f'玩家队列{self.p.player_list}')
                    print(f'房主队列{self.p.room_host_list}')
                    # 输出
                    self.export_json()
                # 离开房间
                if text.find("left the game") != -1:
                    # 尝试
                    try:
                        playerid = re.findall(r'.*(?= left the game)', text)[0]
                    except:
                        playerid = ""
                    print(f'玩家{playerid}离开房间')
                    # 不移除房主队列
                    self.p.remove_player(playerid)
                    # 房主离开立刻更换房主
                    if playerid == self.p.room_host and len(self.p.player_list) != 0:
                        self.p.host_rotate(connection, event)
                    print(f'玩家队列{self.p.player_list}')
                    # 输出
                    self.export_json()
                # 修改 on_pubmsg 方法中处理玩家列表的部分
                if text.find("Slot") != -1:
                    players = re.findall(r'Slot \d+\s+(?:Not Ready|Ready)\s+(https://osu\.ppy\.sh/u/\d+\s+.+)', text)
                    if players:
                        for player_info in players:
                            player_id = self.p.extract_player_name(player_info)
                            if player_id:
                                self.p.add_host(player_id)
                                self.p.add_player(player_id)
                        print(f'玩家队列{self.p.player_list}')
                        print(f'房主队列{self.p.room_host_list}')
                        # 输出
                        self.export_json()
                # 这个是加入房间后要把当前beatmap_id给change一下
                if text.find("Beatmap:") != -1:
                    match = re.search(r'https://osu\.ppy\.sh/b/(\d+)', text)
                    if match:
                        beatmap_id = match.group(1)
                        self.b.change_beatmap_id(beatmap_id)

                # 谱面变化
                if text.find("Beatmap changed to") != -1:
                    # 尝试
                    try:
                        beatmap_url = re.findall(r'(?<=\()\S+(?=\))', text)[0]
                        beatmap_id = re.findall(r'\d+', beatmap_url)[0]
                    except:
                        beatmap_url = ""
                        beatmap_id = ""

                    last_beatmap_id = self.b.beatmap_id
                    if last_beatmap_id == "":
                        last_beatmap_id = "3459231"
                    self.b.change_beatmap_id(beatmap_id)
                    # 获取谱面信息
                    self.b.get_token()
                    self.b.get_beatmap_info()

                    if self.b.check_beatmap_if_out_of_star():
                        self.r.send_msg(connection, event,
                                   f'{self.b.beatmap_star}*>{self.config.starlimit}* 请重新选择')
                        self.r.change_beatmap_to(connection, event, last_beatmap_id)
                        self.b.change_beatmap_id(last_beatmap_id)
                        return
                    if self.b.check_beatmap_if_out_of_time():
                        self.r.send_msg(connection, event,
                                   f'{self.b.beatmap_length}s>{self.config.timelimit}s 请重新选择')
                        self.r.change_beatmap_to(connection, event, last_beatmap_id)
                        self.b.change_beatmap_id(last_beatmap_id)
                        return

                    self.r.send_msg(connection, event, self.b.return_beatmap_info())
                    # 输出
                    self.export_json()

                    predict_result = self.b.predict_beatmap_type(self.b.beatmap_id)

                    result_text = ""

                    for key, value in predict_result[self.b.beatmap_id].items():
                        beatmap_type = key
                        beatmap_possibility = value * 100
                        result_text += f'{beatmap_type}的概率为{beatmap_possibility:.2f}% '

                    self.r.send_msg(connection, event, result_text)

                # 房主变化
                if text.find("became the host") != -1:
                    # 尝试
                    try:
                        self.p.room_host = re.findall(
                            r'.*(?= became the host)', text)[0]
                    except:
                        self.p.room_host = ""
                    print(f'房主变为{self.p.room_host}')

                # 准备就绪，开始游戏
                if text.find("All players are ready") != -1:
                    self.r.start_room(connection, event)

                # 开始游戏
                if text.find("The match has started") != -1:
                    # 将房主队列第一个人移动到最后
                    self.p.host_rotate_pending(connection, event)
                    print(f'游戏开始，房主队列{self.p.room_host_list}')
                    self.p.clear_approved_list()
                    # 获取游戏开始时间
                    self.r.set_game_start_time()

                # 游戏结束,更换房主
                if text.find("The match has finished") != -1:
                    # 对比房主队列,去除离开的玩家,更新房主队列
                    self.p.host_rotate(connection, event)
                    print(f'游戏结束，房主队列{self.p.room_host_list}')
                    # 换了房主以后立即清空投票列表
                    self.p.approved_host_rotate_list.clear()
                    self.p.clear_approved_list()
                    # 发送队列
                    self.p.convert_host()
                    self.r.send_msg(connection, event, str(
                        f'当前队列：{self.p.room_host_list_apprence_text}'))
                    # 重置游戏开始时间
                    self.r.reset_game_start_time()

                # 游戏被丢弃
                if text.find("Aborted the match") != -1:
                    # 判断游戏是否结束
                    timeleft = int(b.beatmap_length)+10 - \
                        int((datetime.now()-r.game_start_time).seconds)
                    if timeleft > 0:  # 大于0代表没打，先不更换房主，退回队列
                        self.p.reverse_host_pending(connection, event)
                        print("比赛被丢弃，房主队列退回")
                    else:  # 小于0代表已经打完，更换房主
                        # 对比房主队列,去除离开的玩家,更新房主队列
                        self.p.host_rotate(connection, event)
                    print(f'游戏结束，房主队列{self.p.room_host_list}')
                    # 换了房主以后立即清空投票列表
                    self.p.approved_host_rotate_list.clear()
                    self.p.clear_approved_list()
                    # 发送队列
                    self.p.convert_host()
                    self.r.send_msg(connection, event, str(
                        f'当前队列：{p.room_host_list_apprence_text}'))
                    # 重置游戏开始时间
                    self.r.reset_game_start_time()
                # bancho重启
                if text.find("Bancho will be right back!") != -1:
                    self.r.send_msg(connection, event,
                               "Bancho重启中，房间将在2min后自动重启")
                    self.restarting_task.start()

            # 玩家发送的消息响应部分

            # 投票丢弃游戏
            if text in ["!abort", "！abort", "!ABORT", "！ABORT"]:
                self.p.vote_for_abort(connection, event)

            # 投票开始游戏
            if text in ["!start", "！start", "!START", "！START"]:
                self.p.vote_for_start(connection, event)

            # 投票跳过房主
            if text in ["!skip", "！skip", "!SKIP", "！SKIP"]:
                self.p.vote_for_host_rotate(connection, event)

            # 投票关闭房间s
            if text in ["!close", "！close", "!CLOSE", "！CLOSE"]:
                self.p.vote_for_close_room(connection, event)

            # 手动查看队列，就只返回前面剩余多少人
            if text in ["!queue", "！queue", "!QUEUE", "！QUEUE", "!q", "！q", "!Q", "！Q"]:
                self.p.convert_host(connection, event)
                index = self.p.remain_hosts_to_player(event.source.split('!')[0])
                self.r.send_msg(connection, event, str(
                    f'你前面剩余人数：{index}'))

            # 帮助
            if text in ["help", "HELP", "!help", "！help", "!HELP", "！HELP", "!h", "！h", "!H", "！H"]:
                self.r.send_msg(connection, event, self.r.help())

            # ping
            if text in ["ping", "PING", "!ping", "！ping", "!PING", "！PING"]:
                self.r.send_msg(connection, event, r'pong')

            # 快速查询成绩
            if text in ["!pr", "！pr", "!PR", "！PR", "!p", "！p", "!P", "！P"]:
                self.b.get_user_id(event.source.split('!')[0])
                detail_1 = self.b.get_recent_info(event.source.split('!')[0])
                self.pp.get_beatmap_file(beatmap_id=self.b.pr_beatmap_id)
                print(self.b.pr_mods)
                detail_2 = self.pp.calculate_pp_obj(
                    mods=self.b.pr_mods, combo=self.b.pr_maxcombo, acc=self.b.pr_acc, misses=self.b.pr_miss)
                self.r.send_msg(connection, event, detail_1)
                self.r.send_msg(connection, event, detail_2)

            # 快速当前谱面成绩
            if text in ["!s", "！s", "!S", "！S"]:
                self.b.get_user_id(event.source.split('!')[0])
                s = self.b.get_beatmap_score(event.source.split('!')[0])
                self.r.send_msg(connection, event, s)
                if s.find("未查询到") == -1:
                    self.pp.get_beatmap_file(beatmap_id=self.b.beatmap_id)
                    self.r.send_msg(connection, event, self.pp.calculate_pp_obj(
                        mods=self.b.pr_mods, combo=self.b.pr_maxcombo, acc=self.b.pr_acc, misses=self.b.pr_miss))

            # 快速查询谱面得分情况
            if text.find("!m+") != -1 or text.find("！m+") != -1 or text.find("!M+") != -1 or text.find("！M+") != -1:
                try:
                    modslist_str = re.findall(r'\+(.*)', event.arguments[0])[0]
                except:
                    modslist_str = ""
                self.pp.get_beatmap_file(beatmap_id=self.b.beatmap_id)
                self.r.send_msg(connection, event, self.pp.calculate_pp_fully(modslist_str))

            if text in ["!m", "！m", "!M", "！M"]:
                self.pp.get_beatmap_file(beatmap_id=self.b.beatmap_id)
                self.r.send_msg(connection, event,self.pp.calculate_pp_fully(''))

            # 快速获取剩余时间 大约10s游戏延迟
            if text in ["!ttl", "！ttl", "!TTL", "！TTL"]:
                if b.beatmap_length != "" and r.game_start_time != "":
                    timeleft = int(b.beatmap_length)+10 - \
                        int((datetime.now()-self.r.game_start_time).seconds)
                    self.r.send_msg(connection, event, f'剩余游玩时间：{timeleft}s')
                else:
                    self.r.send_msg(connection, event, f'剩余游玩时间：未知')

            if text in ["!i", "！i"]:
                self.b.get_token()
                self.b.get_beatmap_info()
                self.r.send_msg(connection, event, self.b.return_beatmap_info())

                predict_result = self.b.predict_beatmap_type(self.b.beatmap_id)

                result_text = ""

                for key, value in predict_result[self.b.beatmap_id].items():
                    beatmap_type = key
                    beatmap_possibility = value * 100
                    result_text += f'{beatmap_type}的概率为{beatmap_possibility:.2f}% '

                self.r.send_msg(connection, event, result_text)


            if text in ["!about", "！about", "!ABOUT", "！ABORT"]:
                self.r.send_msg(connection, event,
                           "[https://github.com/Ohdmire/osu-ircbot-py ATRI高性能bot]")

        except Exception as e:
            print(f'-----------------未知错误---------------------\n{e}')


# 定义玩家类
class Player:
    def __init__(self):
        self.player_list = []
        self.room_host_list = []
        self.room_host_list_apprence_text = ""
        self.approved_abort_list = []
        self.approved_start_list = []
        self.approved_host_rotate_list = []
        self.approved_close_list = []
        self.room_host = ""

    def add_player(self, name):
        if name not in self.player_list:
            self.player_list.append(name)

    def add_host(self, name):
        if name not in self.room_host_list:
            self.room_host_list.append(name)

    def remove_host(self, name):
        if name in self.room_host_list:
            self.room_host_list.remove(name)

    def remain_hosts_to_player(self, name):
        name_normalized = name.replace(" ", "_")
        for index, host in enumerate(self.room_host_list):
            host_normalized = host.replace(" ", "_")
            if name_normalized == host_normalized:
                return index
        print(f"{name} is not in room_host_list", self.room_host_list)
        return -1  # 如果没有找到匹配的名字，返回-1

    def extract_player_name(self, text):
        match = re.search(r'https://osu\.ppy\.sh/u/\d+\s*(.*?)(?:\s*\[.*\])?$', text)
        if match:
            playername = match.group(1).strip()
            return playername
        return ""

    def convert_host(self):
        try:
            self.room_host_list_apprence_text = ""
            for index, host in enumerate(self.room_host_list):
                if index == 0:
                    self.room_host_list_apprence_text += host
                else:
                    # 在每个字符之间插入零宽空格
                    self.room_host_list_apprence_text += "\u200B".join(host)
                
                if index < len(self.room_host_list) - 1:
                    self.room_host_list_apprence_text += "-->"
        except:
            print("房主队列转换失败")

    def remove_player(self, name):
        if name in self.player_list:
            self.player_list.remove(name)

    def reset_player_list(self):
        self.player_list.clear()

    def reset_host_list(self):
        self.room_host_list.clear()

    def clear_approved_list(self):
        self.approved_abort_list.clear()
        self.approved_start_list.clear()
        self.approved_close_list.clear()
        self.approved_host_rotate_list.clear()

    def host_rotate_pending(self, connection, event):
        now_host = self.room_host_list[0]
        self.remove_host(now_host)
        self.add_host(now_host)

    def reverse_host_pending(self, connection, event):
        self.remove_host(self.room_host)
        self.room_host_list.insert(0, self.room_host)

    def host_rotate(self, connection, event):
        result_list = []
        for i in self.room_host_list:
            if i in self.player_list:
                result_list.append(i)
        self.room_host_list = result_list
        r.change_host(connection, event, self.room_host_list[0])

    def vote_for_abort(self, connection, event):
        # 获取发送者名字
        name = event.source.split('!')[0]
        if name not in self.approved_abort_list:
            self.approved_abort_list.append(name)
        if len(self.approved_abort_list) >= round(len(self.player_list)/2):
            r.abort_room(connection, event)
            self.approved_abort_list.clear()
        else:
            r.send_msg(connection, event, r'输入!abort强制放弃比赛 {} / {} '.format(
                str(len(self.approved_abort_list)), str(round(len(self.player_list)/2))))

    def vote_for_start(self, connection, event):
        # 获取发送者名字
        name = event.source.split('!')[0]
        if name not in self.approved_start_list:
            self.approved_start_list.append(name)
        if len(self.approved_start_list) >= round(len(self.player_list)/2):
            r.start_room(connection, event)
            self.approved_start_list.clear()
        else:
            r.send_msg(connection, event, r'输入!start强制开始比赛 {} / {} '.format(
                str(len(self.approved_start_list)), str(round(len(self.player_list)/2))))

    def vote_for_host_rotate(self, connection, event):
        # 获取发送者名字
        name = event.source.split('!')[0]
        # 如果发送者是房主，直接换房主
        if name == self.room_host:
            self.host_rotate_pending(connection, event)
            self.host_rotate(connection, event)
            self.approved_host_rotate_list.clear()
            print("房主自行更换")
            return
        if name not in self.approved_host_rotate_list:
            self.approved_host_rotate_list.append(name)
        if len(self.approved_host_rotate_list) >= round(len(self.player_list)/2):
            self.host_rotate_pending(connection, event)
            self.host_rotate(connection, event)
            self.approved_host_rotate_list.clear()
        else:
            r.send_msg(connection, event, r'输入!skip强制跳过房主 {} / {} '.format(
                str(len(self.approved_host_rotate_list)), str(round(len(self.player_list)/2))))

    def vote_for_close_room(self, connection, event):
        # 获取发送者名字
        name = event.source.split('!')[0]
        if name not in self.approved_close_list:
            self.approved_close_list.append(name)
        if len(self.approved_close_list) == len(self.player_list):
            r.close_room(connection, event)
            self.approved_close_list.clear()
        else:
            r.send_msg(connection, event, r'输入!close强制关闭房间(1min后自动重启) {} / {} '.format(
                str(len(self.approved_close_list)), str(len(self.player_list))))


# 定义房间操作类
class Room:
    def __init__(self, config):
        self.room_id = ""
        self.last_romm_id = ""
        self.game_start_time = ""
        self.config = config

    def set_game_start_time(self):
        self.game_start_time = datetime.now()
        return self.game_start_time

    def reset_game_start_time(self):
        self.game_start_time = ""

    def get_last_room_id(self):
        try:
            with open('last_room_id.txt', 'r') as f:
                self.last_romm_id = f.read()
                print(f'获取上一个房间ID{self.last_romm_id}')
        except:
            print("未获取上一个房间ID")
        return self.last_romm_id

    # 保存当前id到文件
    def save_last_room_id(self):
        try:
            with open('last_room_id.txt', 'w') as f:
                f.write(self.room_id)
                print(f'保存当前房间ID{self.room_id}')
        except:
            print("未保存当前房间ID")

    def help(self):
        return r'!queue(!q) 查看队列 | !abort 投票丢弃游戏 | !start 投票开始游戏 | !skip 投票跳过房主 | !pr(!p) 查询最近成绩 | !s 查询当前谱面bp | !m+{MODS} 查询谱面模组PP| !i 返回当前谱面信息| !ttl 查询剩余时间 | !close 投票关闭(1min后自动重启)房间 | help(!h) 查看帮助 | !about 关于机器人'

    def change_room_id(self, id):
        self.room_id = id
        print(f'更换当前房间ID为{self.room_id}')

    def send_msg(self, connection, evetn, msg_text):
        connection.privmsg(self.room_id, msg_text)
        print("发送消息："+msg_text)

    def create_room(self, connection, event):
        connection.privmsg(
            "BanchoBot", "!mp make "+self.config.mpname)
        print("创建房间")

    def join_room(self, connection, event):
        connection.join(self.room_id)  # 加入 #osu 频道
        print(f'加入房间{self.room_id}')

    def close_room(self, connection, event):
        connection.privmsg(self.room_id, "!mp close")
        print(f'关闭房间{self.room_id}')

    def change_host(self, connection, event, playerid):
        connection.privmsg(self.room_id, "!mp host "+playerid)
        print("更换房主为 "+playerid)

    def start_room(self, connection, event):
        connection.privmsg(self.room_id, "!mp start")
        print("开始游戏")

    def abort_room(self, connection, event):
        connection.privmsg(self.room_id, "!mp abort")
        print("丢弃游戏")

    def change_password(self, connection, event):
        connection.privmsg(self.room_id, "!mp password "+self.config.mppassword)
        print("修改密码")

    def change_beatmap_to(self, connection, event, beatmapid):
        connection.privmsg(self.room_id, "!mp map "+beatmapid)
        print("更换谱面为"+beatmapid)

    def change_mods_to_FM(self, connection, event):
        connection.privmsg(self.room_id, "!mp mods FreeMod")
        print("开启Freemod")

    def get_mp_settings(self, connection, event):
        connection.privmsg(self.room_id, "!mp settings")
        print("获取房间详情成功")


# 定义谱面类
class Beatmap:
    def __init__(self, config):
        self.osu_client_id = config.osuclientid
        self.osu_client_secret = config.osuclientsecret
        self.config = config
        self.osu_token = ""
        self.beatmap_id = ""
        self.beatmap_songs_id = ""
        self.beatmap_name = ""
        self.beatmap_artist = ""
        self.beatmap_star = 0
        self.beatmap_status = ""
        self.beatemap_bpm = ""
        self.beatmap_cs = ""
        self.beatmap_ar = ""
        self.beatmap_od = ""
        self.beatmap_hp = ""
        self.beatmap_length = 0
        self.beatmap_ranked_date = ""
        self.beatmatp_submit_date = ""
        self.beatmap_mirror_sayo_url = ""
        self.beatmap_osudirect_url = ""

        self.id2name = {}

        self.pr_beatmap_id = ""
        self.pr_beatmap_url = ""

        self.pr_title = ""
        self.pr_artist = ""
        self.pr_star = ""

        self.pr_acc = 0
        self.pr_maxcombo = 0
        self.pr_300 = 0
        self.pr_100 = 0
        self.pr_50 = 0
        self.pr_miss = 0
        self.pr_pp = 0
        self.pr_rank = ""
        self.pr_mods = ""

        self.pr_username = ""

    def clear_cache(self):
        self.osu_token = ""
        self.id2name.clear()

    def get_token(self):
        try:
            url = 'https://osu.ppy.sh/oauth/token'
            data = {
                "client_id": self.osu_client_id,
                "client_secret": self.osu_client_secret,
                "grant_type": "client_credentials",
                "scope": "public"
            }
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json"
            }
            response = requests.post(url, data=data, headers=headers)
            response.raise_for_status()  # 如果请求失败，这会抛出一个异常
            self.osu_token = response.json()['access_token']
        except Exception as e:
            self.osu_token = ""
            print(f"获取访问令牌失败，错误信息：{e}")

    def predict_beatmap_type(self, beatmap_id):
    
        data = {"beatmap_ids": [beatmap_id]}
        url = self.config.predict_url
        if url == "":
            return ""
        response = requests.post(url, json=data)
        response.raise_for_status()
        return response.json()

    # 使用访问令牌查询
    def get_beatmap_info(self):

        try:
            url = f'https://osu.ppy.sh/api/v2/beatmaps/'+self.beatmap_id
            headers = {'Authorization': f'Bearer {self.osu_token}'}
            response = requests.get(url, headers=headers)
            response.raise_for_status()  # 如果请求失败，这会抛出一个异常

            self.beatmap_songs_id = str(response.json()['beatmapset_id'])

            self.beatmap_name = response.json()['beatmapset']['title_unicode']
            self.beatmap_artist = response.json()['beatmapset']['artist_unicode']
            self.beatmap_star = response.json()['difficulty_rating']
            self.beatmap_status = response.json()['status']
            self.beatmap_bpm = response.json()['bpm']
            self.beatmap_cs = response.json()['cs']
            self.beatmap_ar = response.json()['ar']
            self.beatmap_od = response.json()['accuracy']
            self.beatmap_hp = response.json()['drain']
            self.beatmap_length = response.json()['total_length']
            if self.beatmap_status == "ranked":
                self.beatmap_ranked_date = response.json(
                )['beatmapset']['ranked_date'][:10]
            else:
                self.beatmap_ranked_date = response.json(
                )['beatmapset']['submitted_date'][:10]
            self.beatmap_mirror_sayo_url = "https://osu.sayobot.cn/home?search="+self.beatmap_songs_id
            self.beatmap_mirror_inso_url = "http://inso.link/yukiho/?b="+self.beatmap_id
            self.beatmap_osudirect_url = response.json()['url']
        except Exception as e:
            print(f'获取谱面信息失败，原因：{e}')
            self.beatmap_name = "获取谱面信息失败"
            self.beatmap_songs_id = ""
            self.beatmap_artist = ""
            self.beatmap_star = 0
            self.beatmap_status = ""
            self.beatmap_bpm = ""
            self.beatmap_cs = ""
            self.beatmap_ar = ""
            self.beatmap_od = ""
            self.beatmap_hp = ""
            self.beatmap_length = 0
            self.beatmap_ranked_date = ""
            self.beatmap_mirror_sayo_url = ""
            self.beatmap_mirror_inso_url = ""
            self.beatmap_osudirect_url = ""

    def change_beatmap_id(self, id):
        self.beatmap_id = id
        print(f'更换谱面ID为 {self.beatmap_id}')

    def check_beatmap_if_out_of_star(self):
        if float(self.config.starlimit) == 0:
            return False
        if self.beatmap_star > float(self.config.starlimit):
            return True
        else:
            return False

    def check_beatmap_if_out_of_time(self):
        if float(self.config.timelimit) == 0:
            return False
        if self.beatmap_length > float(self.config.timelimit):
            return True
        else:
            return False

    def return_beatmap_info(self):
        result = r'{} {}| {}*| [{} {} - {}]| bpm:{} length:{}s| ar:{} cs:{} od:{} hp:{}| [{} Sayobot] OR [{} inso]'.format(self.beatmap_ranked_date, self.beatmap_status, self.beatmap_star, self.beatmap_osudirect_url,
                                                                                                                           self.beatmap_name, self.beatmap_artist, self.beatemap_bpm, self.beatmap_length, self.beatmap_ar, self.beatmap_cs, self.beatmap_od, self.beatmap_hp, self.beatmap_mirror_sayo_url, self.beatmap_mirror_inso_url)
        print(result)
        return result

    def get_match_info(self, match_id):
        try:
            url = f'https://osu.ppy.sh/api/v2/matches/{match_id}'
            headers = {'Authorization': f'Bearer {self.osu_token}'}
            response = requests.get(url, headers=headers)
            response.raise_for_status()  # 如果请求失败，这将抛出一个异常
            return response.json()
        except:
            print("获取比赛信息失败")
            return ""

    def get_user_id(self, username):
        try:
            if username not in self.id2name:
                print("获取用户ID")
                url = f'https://osu.ppy.sh/api/v2/users/{username}?key=username'
                headers = {'Authorization': f'Bearer {self.osu_token}'}
                response = requests.get(url, headers=headers)
                response.raise_for_status()  # 如果请求失败，这将抛出一个异常
                self.id2name[username] = response.json()['id']
                print(self.id2name)
        except:
            print("获取用户ID失败")

    def get_beatmap_score(self, username):
        try:
            user_id = self.id2name[username]
            url = f"https://osu.ppy.sh/api/v2/beatmaps/{self.beatmap_id}/scores/users/{user_id}"
            headers = {'Authorization': f'Bearer {self.osu_token}'}
            response = requests.get(url, headers=headers)
            response.raise_for_status()  # 如果请求失败，这会抛出一个异常

            self.pr_title = self.beatmap_name
            self.pr_artist = self.beatmap_artist
            self.pr_star = self.beatmap_star

            self.beatmap_score_created_at = response.json()[
                'score']['created_at'][:10]

            self.pr_acc = response.json()['score']['accuracy']
            self.pr_maxcombo = response.json()['score']['max_combo']
            self.pr_300 = response.json()['score']['statistics']['count_300']
            self.pr_100 = response.json()['score']['statistics']['count_100']
            self.pr_50 = response.json()['score']['statistics']['count_50']
            self.pr_miss = response.json()['score']['statistics']['count_miss']
            self.pr_pp = response.json()['score']['pp']
            self.pr_rank = response.json()['score']['rank']
            self.pr_mods = response.json()['score']['mods']
            self.pr_mods = "".join([str(mod) for mod in self.pr_mods])

            self.pr_beatmap_url = response.json()['score']['beatmap']['url']

            self.pr_username = username

            self.pr_acc = round(self.pr_acc*100, 2)

        except HTTPError:
            print(f"未查询到{username}在该谱面上留下的成绩")
            return f"未查询到{username}在该谱面上留下的成绩"

        except Exception as e:
            print(f'获取谱面成绩失败，错误信息：{e}')
            self.pr_title = "获取谱面成绩失败"
            self.pr_artist = ""
            self.pr_star = ""
            self.pr_acc = 0
            self.pr_maxcombo = 0
            self.pr_300 = 0
            self.pr_100 = 0
            self.pr_50 = 0
            self.pr_miss = 0
            self.pr_pp = 0
            self.pr_rank = ""
            self.pr_mods = ""
            self.pr_username = ""

            self.beatmap_score_created_at = ""

        result = r'{}| [{} {} - {}]| {}*| {} [ {} ] {}pp acc:{}% combo:{}x| {}/{}/{}/{}| date:{}|'.format(
            self.pr_username, self.pr_beatmap_url, self.pr_title, self.pr_artist, self.pr_star, self.pr_mods, self.pr_rank, self.pr_pp, self.pr_acc, self.pr_maxcombo, self.pr_300, self.pr_100, self.pr_50, self.pr_miss, self.beatmap_score_created_at)
        print(result)
        return result

    def get_recent_info(self, username):
        try:
            user_id = self.id2name[username]
            url = f'https://osu.ppy.sh/api/v2/users/{user_id}/scores/recent?&include_fails=1'
            headers = {'Authorization': f'Bearer {self.osu_token}'}
            response = requests.get(url, headers=headers)
            response.raise_for_status()  # 如果请求失败，这将抛出一个异常

            self.pr_beatmap_id = response.json()[0]['beatmap']['id']
            self.pr_title = response.json()[0]['beatmapset']['title_unicode']
            self.pr_artist = response.json()[0]['beatmapset']['artist_unicode']
            self.pr_star = response.json()[0]['beatmap']['difficulty_rating']

            self.pr_acc = response.json()[0]['accuracy']
            self.pr_maxcombo = response.json()[0]['max_combo']
            self.pr_300 = response.json()[0]['statistics']['count_300']
            self.pr_100 = response.json()[0]['statistics']['count_100']
            self.pr_50 = response.json()[0]['statistics']['count_50']
            self.pr_miss = response.json()[0]['statistics']['count_miss']
            self.pr_pp = response.json()[0]['pp']
            self.pr_rank = response.json()[0]['rank']
            self.pr_mods = response.json()[0]['mods']
            self.pr_mods = "".join([str(mod) for mod in self.pr_mods])

            self.pr_beatmap_url = response.json()[0]['beatmap']['url']

            self.pr_username = username

            self.pr_acc = round(self.pr_acc*100, 2)

        except Exception as e:
            print(f'获取最近成绩失败，错误信息：{e}')
            self.pr_title = "获取最近成绩失败"
            self.pr_artist = ""
            self.pr_star = ""
            self.pr_acc = 0
            self.pr_maxcombo = 0
            self.pr_300 = 0
            self.pr_100 = 0
            self.pr_50 = 0
            self.pr_miss = 0
            self.pr_pp = 0
            self.pr_rank = ""
            self.pr_mods = ""
            self.pr_username = ""
            self.pr_beatmap_url = ""

        result = r'{}| [{} {} - {}]| {}*| {} [ {} ] {}pp acc:{}% combo:{}x| {}/{}/{}/{}|'.format(
            self.pr_username, self.pr_beatmap_url, self.pr_title, self.pr_artist, self.pr_star, self.pr_mods, self.pr_rank, self.pr_pp, self.pr_acc, self.pr_maxcombo, self.pr_300, self.pr_100, self.pr_50, self.pr_miss,)
        print(result)
        return result


class PP:
    def __init__(self):
        self.beatmap_id = ""
        self.mods = 0
        self.acc = 0
        self.combo = 0
        self.misses = 0

        self.maxbeatmapcombo = 0

        self.stars = 0

        self.maxpp = 0
        self.maxaimpp = 0
        self.maxspeedpp = 0
        self.maxaccpp = 0

        self.afterar = 0
        self.aftercs = 0
        self.afterod = 0
        self.afterhp = 0

        self.currpp = 0
        self.curraimpp = 0
        self.currspeedpp = 0
        self.curraccpp = 0

        self.fcpp = 0
        self.fc95pp = 0
        self.fc96pp = 0
        self.fc97pp = 0
        self.fc98pp = 0
        self.fc99pp = 0


    def get_beatmap_file(self, beatmap_id):
        self.beatmap_id = beatmap_id

        if os.path.exists(f'./maps/{beatmap_id}.osu'):
            print(f'谱面文件已存在')
        else:
            try:
                url = f'https://osu.ppy.sh/osu/{beatmap_id}'
                response = requests.get(url)
                response.raise_for_status()  # 如果请求失败，这会抛出一个异常
                with open(f'./maps/{beatmap_id}.osu', 'wb') as f:
                    f.write(response.content)
            except:
                print("获取谱面文件失败")

    def calculate_pp_fully(self, mods):
        try:
            self.mods = mods
            beatmap = rosu.Beatmap(path=f"./maps/{self.beatmap_id}.osu")
            max_perf = rosu.Performance(mods=mods)
            attrs = max_perf.calculate(beatmap)
            self.maxpp = attrs.pp

            # 计算maxbeatmapcombo
            self.maxbeatmapcombo = attrs.difficulty.max_combo

            # 计算stars
            self.stars = attrs.difficulty.stars

            # 计算4维
            beatmap_attr_builder = rosu.BeatmapAttributesBuilder(mods=mods)
            beatmap_attr_builder.set_map(beatmap)
            beatmap_attr = beatmap_attr_builder.build()
            self.afterar = beatmap_attr.ar
            self.aftercs = beatmap_attr.cs
            self.afterod = beatmap_attr.od
            self.afterhp = beatmap_attr.hp

            # 计算if 95% pp
            max_perf.set_accuracy(95)
            fc95_perf = max_perf.calculate(beatmap)
            self.fc95pp = fc95_perf.pp

            # 计算if 96% pp
            max_perf.set_accuracy(96)
            fc96_perf = max_perf.calculate(beatmap)
            self.fc96pp = fc96_perf.pp

            # 计算if 97% pp
            max_perf.set_accuracy(97)
            fc97_perf = max_perf.calculate(beatmap)
            self.fc97pp = fc97_perf.pp

            # 计算if 98% pp
            max_perf.set_accuracy(98)
            fc98_perf = max_perf.calculate(beatmap)
            self.fc98pp = fc98_perf.pp

            # 计算if 99% pp
            max_perf.set_accuracy(99)
            fc99_perf = max_perf.calculate(beatmap)
            self.fc99pp = fc99_perf.pp

            self.maxpp = round(self.maxpp)
            self.fc95pp = round(self.fc95pp)
            self.fc96pp = round(self.fc96pp)
            self.fc97pp = round(self.fc97pp)
            self.fc98pp = round(self.fc98pp)
            self.fc99pp = round(self.fc99pp)
            self.stars = round(self.stars, 2)

            self.afterar = round(self.afterar, 1)
            self.aftercs = round(self.aftercs, 1)
            self.afterod = round(self.afterod, 1)
            self.afterhp = round(self.afterhp, 1)

        except:
            print("计算pp失败")
            self.maxpp = 0
            self.maxbeatmapcombo = 0
            self.fc95pp = 0
            self.fc96pp = 0
            self.fc97pp = 0
            self.fc98pp = 0
            self.fc99pp = 0
            self.stars = 0

            self.afterar = 0
            self.aftercs = 0
            self.afterod = 0
            self.afterhp = 0

        return f'{self.mods}| {self.stars}*| {self.maxbeatmapcombo}x| ar:{self.afterar} cs:{self.aftercs} od:{self.afterod} hp:{self.afterhp} | SS:{self.maxpp}pp| 99%:{self.fc99pp}pp| 98%:{self.fc98pp}pp| 97%:{self.fc97pp}pp| 96%:{self.fc96pp}pp| 95%:{self.fc95pp}pp'

    def calculate_pp_obj(self, mods, acc, misses, combo):

        try:

            self.mods = mods

            map = rosu.Beatmap(path=f"./maps/{self.beatmap_id}.osu")

            max_perf = rosu.Performance(mods=mods)

            attrs = max_perf.calculate(map)

            self.maxpp = attrs.pp

            self.maxbeatmapcombo = attrs.difficulty.max_combo

            self.maxaimpp = attrs.pp_aim
            self.maxspeedpp = attrs.pp_speed
            self.maxaccpp = attrs.pp_accuracy

            # 计算玩家的current performance
            max_perf.set_misses(misses)
            max_perf.set_accuracy(acc)
            max_perf.set_combo(combo)

            curr_perf = max_perf.calculate(map)
            self.currpp = curr_perf.pp
            self.curraccpp = curr_perf.pp
            self.curraimpp = curr_perf.pp_aim
            self.currspeedpp = curr_perf.pp_speed
            self.curraccpp = curr_perf.pp_accuracy

            # 计算if fc pp
            max_perf.set_misses(0)
            max_perf.set_combo(None)

            fc_perf = max_perf.calculate(map)
            self.fcpp = fc_perf.pp

            # 计算if 95% pp
            max_perf.set_accuracy(95)
            fc95_perf = max_perf.calculate(map)
            self.fc95pp = fc95_perf.pp

            # 计算if 96% pp
            max_perf.set_accuracy(96)
            fc96_perf = max_perf.calculate(map)
            self.fc96pp = fc96_perf.pp

            # 计算if 97% pp
            max_perf.set_accuracy(97)
            fc97_perf = max_perf.calculate(map)
            self.fc97pp = fc97_perf.pp

            # 计算if 98% pp
            max_perf.set_accuracy(98)
            fc98_perf = max_perf.calculate(map)
            self.fc98pp = fc98_perf.pp

            # 计算if 99% pp
            max_perf.set_accuracy(99)
            fc99_perf = max_perf.calculate(map)
            self.fc99pp = fc99_perf.pp

            self.maxpp = round(self.maxpp)
            self.maxaimpp = round(self.maxaimpp)
            self.maxspeedpp = round(self.maxspeedpp)
            self.maxaccpp = round(self.maxaccpp)

            self.currpp = round(self.currpp)
            self.curraimpp = round(self.curraimpp)
            self.currspeedpp = round(self.currspeedpp)
            self.curraccpp = round(self.curraccpp)

            self.fcpp = round(self.fcpp)
            self.fc95pp = round(self.fc95pp)
            self.fc96pp = round(self.fc96pp)
            self.fc97pp = round(self.fc97pp)
            self.fc98pp = round(self.fc98pp)
            self.fc99pp = round(self.fc99pp)

        except Exception as e:
            print(f'计算pp失败: {e}')
            self.maxpp = 0
            self.maxaimpp = 0
            self.maxspeedpp = 0
            self.maxaccpp = 0

            self.maxbeatmapcombo = 0

            self.currpp = 0
            self.curraimpp = 0
            self.currspeedpp = 0
            self.curraccpp = 0

            self.fcpp = 0
            self.fc95pp = 0
            self.fc96pp = 0
            self.fc97pp = 0
            self.fc98pp = 0
            self.fc99pp = 0

        return f'now:{self.currpp}pp| if FC({self.maxbeatmapcombo}x):{self.fcpp}pp| 95%:{self.fc95pp}pp| 96%:{self.fc96pp}pp| 97%:{self.fc97pp}pp| 98%:{self.fc98pp}pp| 99%:{self.fc99pp}pp| SS:{self.maxpp}pp| aim:{self.curraimpp}/{self.maxaimpp}pp| speed:{self.currspeedpp}/{self.maxspeedpp}pp| acc:{self.curraccpp}/{self.maxaccpp}pp'


if __name__ == '__main__':
    # 没有maps文件夹时自动创建maps文件夹
    maps_dir = os.path.join(os.getcwd(), './maps')
    if not os.path.exists(maps_dir):
        os.makedirs(maps_dir)
        print(f"'{maps_dir}'文件夹不存在，已经自动创建")

    config = Config()

    p = Player()
    r = Room(config)
    b = Beatmap(config)
    pp = PP()

    client = MyIRCClient(osu_server, osu_port, config, p, r, b, pp)
    client.start()
