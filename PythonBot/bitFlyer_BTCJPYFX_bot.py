#_*_ coding: utf-8 _*_
### Modified FXPlan.com bitcforex from 2018/12/16
### https://bitcforex.com
### 下記転用
### https://note.mu/ycrypthon/n/n359de7443211
### "てょんやら"さん:https://note.mu/ycrypthon
import sys,os
import pybitflyer
import json
import requests
import logging
import csv
import math
import pandas as pd
import time
import requests
import datetime
from pubnub.callbacks import SubscribeCallback
from pubnub.enums import PNStatusCategory
from pubnub.pnconfiguration import PNConfiguration
from pubnub.pubnub_tornado import PubNubTornado
from pubnub.pnconfiguration import PNReconnectionPolicy
from tornado import gen
import threading
from collections import deque
### 300 YEN IS STOP_LOSS(POSITION)
LOSS_CUT = -300.0000
#####################
# ログの出力名を設定（1）
logger = logging.getLogger('bitFlyer_BTXJPYFX_bot')
# ログレベルの設定
logger.setLevel(10)
# ログのファイル出力先を設定
fh = logging.FileHandler('bitFlyer_BTXJPYFX_Trace.log')
logger.addHandler(fh)
# ログの出力形式の設定
formatter = logging.Formatter('%(asctime)s:%(lineno)d:%(levelname)s:%(message)s')
fh.setFormatter(formatter)
#####################

class ChannelBreakOut:
    def __init__(self):
        #pubnubから取得した約定履歴を保存するリスト（基本的に不要．）
        self._executions = deque(maxlen=300)
        self._lot = 0.05
        self._margin = 15
        self._product_code = "FX_BTC_JPY"
        #各パラメタ．
        self._entryTerm = 5
        self._closeTerm = 15
        self._rangeTerm = 15
        self._rangeTh = 5000
        self._waitTerm = 5
        self._waitTh = 20000
        self._candleTerm = "1T"
        #現在のポジション．1ならロング．-1ならショート．0ならポジションなし．
        self._pos = 0
        #注文執行コスト．遅延などでこの値幅を最初から取られていると仮定する
        self._cost = 0.1
        self.order = Order()
        self.api = pybitflyer.API("*****************************", "*******************************************")

        #ラインに稼働状況を通知
        self.line_notify_token = '******************************************'
        self.line_notify_api = 'https://notify-api.line.me/api/notify'
        ## 内部計算用損益

    @property
    def cost(self):
        return self._cost

    @cost.setter
    def cost(self, value):
        self._cost = value

    @property
    def candleTerm(self):
        return self._candleTerm
    @candleTerm.setter
    def candleTerm(self, val):
        """
        valは"5T"，"1H"などのString
        """
        self._candleTerm = val

    @property
    def waitTh(self):
        return self._waitTh
    @waitTh.setter
    def waitTh(self, val):
        self._waitTh = val

    @property
    def waitTerm(self):
        return self._waitTerm
    @waitTerm.setter
    def waitTerm(self, val):
        self._waitTerm = val

    @property
    def rangeTh(self):
        return self._rangeTh
    @rangeTh.setter
    def rangeTh(self,val):
        self._rangeTh = val

    @property
    def rangeTerm(self):
        return self._rangeTerm
    @rangeTerm.setter
    def rangeTerm(self,val):
        self._rangeTerm = val


    @property
    def executions(self):
        return self._executions
    @executions.setter
    def executions(self, val):
        self._executions = val

    @property
    def pos(self):
        return self._pos
    @pos.setter
    def pos(self, val):
        self._pos = int(val)

    @property
    def lot(self):
        return self._lot
    @lot.setter
    def lot(self, val):
        self._lot = round(val,3)

    @property
    def product_code(self):
        return self._product_code
    @product_code.setter
    def product_code(self, val):
        self._product_code = val

    @property
    def entryTerm(self):
        return self._entryTerm
    @entryTerm.setter
    def entryTerm(self, val):
        self._entryTerm = int(val)

    @property
    def closeTerm(self):
        return self._closeTerm
    @closeTerm.setter
    def closeTerm(self, val):
        self._closeTerm = int(val)

    def calculateLot(self, margin):
        """
        証拠金からロットを計算する関数．
        """
        lot = math.floor(margin*10**(-4))*10**(-2)*self.cost
        return round(lot,2)

    def calculateLines(self, df_candleStick, term):
        """

        期間高値・安値を計算する．
        candleStickはcryptowatchのローソク足．termは安値，高値を計算する期間．（5ならローソク足5本文の安値，高値．)
        """
        lowLine = []
        highLine = []
        for i in range(len(df_candleStick.index)):
            if i < term:
                lowLine.append(df_candleStick["high"][i])
                highLine.append(df_candleStick["low"][i])
            else:
                low = min([price for price in df_candleStick["low"][i-term:i]])
                high = max([price for price in df_candleStick["high"][i-term:i]])
                lowLine.append(low)
                highLine.append(high)
        return (lowLine, highLine)

    def calculatePriceRange(self, df_candleStick, term):
        """
        termの期間の値幅を計算．
        """
        low = [min([df_candleStick["close"][i-term+1:i].min(),df_candleStick["open"][i-term+1:i].min()]) for i in range(len(df_candleStick.index))]
        high = [max([df_candleStick["close"][i-term+1:i].max(), df_candleStick["open"][i-term+1:i].max()]) for i in range(len(df_candleStick.index))]
        low = pd.Series(low)
        high = pd.Series(high)
        priceRange = [high.iloc[i]-low.iloc[i] for i in range(len(df_candleStick.index))]
        return priceRange

    def isRange(self,df_candleStick ,term, th):
        """
        レンジ相場かどうかをTrue,Falseの配列で返す．termは期間高値・安値の計算期間．thはレンジ判定閾値．
        """
        #値幅での判定．
        if th != None:
            priceRange = self.calculatePriceRange(df_candleStick, term)
            isRange = [th > i for i in priceRange]
        #終値の標準偏差の差分が正か負かでの判定．
        elif th == None and term != None:
            df_candleStick["std"] = [df_candleStick["close"][i-term+1:i].std() for i in range(len(df_candleStick.index))]
            df_candleStick["std_slope"] = [df_candleStick["std"][i]-df_candleStick["std"][i-1] for i in range(len(df_candleStick.index))]
            isRange = [i > 0 for i in df_candleStick["std_slope"]]
        else:
            isRange = [False for i in df_candleStick.index]
        return isRange

    def judge(self, df_candleStick, entryHighLine, entryLowLine, closeHighLine, closeLowLine, entryTerm):
        """
        売り買い判断．ローソク足の高値が期間高値を上抜けたら買いエントリー．（2）ローソク足の安値が期間安値を下抜けたら売りエントリー．judgementリストは[買いエントリー，売りエントリー，買いクローズ（売り），売りクローズ（買い）]のリストになっている．（二次元リスト）リスト内リストはの要素は，0（シグナルなし）,価格（シグナル点灯）を取る．
        """
        judgement = [[0,0,0,0] for i in range(len(df_candleStick.index))]
        for i in range(len(df_candleStick.index)):
            #上抜けでエントリー
            if df_candleStick["high"][i] > entryHighLine[i] and i >= entryTerm:
                judgement[i][0] = entryHighLine[i]
            #下抜けでエントリー
            if df_candleStick["low"][i] < entryLowLine[i] and i >= entryTerm:
                judgement[i][1] = entryLowLine[i]
            #下抜けでクローズ
            if df_candleStick["low"][i] < closeLowLine[i] and i >= entryTerm:
                judgement[i][2] = closeLowLine[i]
            #上抜けでクローズ
            if df_candleStick["high"][i] > closeHighLine[i] and i >= entryTerm:
                judgement[i][3] = closeHighLine[i]
            #
            else:
                pass
        return judgement

    def judgeForLoop(self, high, low, entryHighLine, entryLowLine, closeHighLine, closeLowLine):
        """
        売り買い判断．入力した価格が期間高値より高ければ買いエントリー，期間安値を下抜けたら売りエントリー．judgementリストは[買いエントリー，売りエントリー，買いクローズ（売り），売りクローズ（買い）]のリストになっている．（値は0or1）
        ローソク足は1分ごとに取得するのでインデックスが-1のもの（現在より1本前）をつかう．
        """
        judgement = [0,0,0,0]
        #上抜けでエントリー
        if high > entryHighLine[-1]:
            judgement[0] = 1
        #下抜けでエントリー
        if low < entryLowLine[-1]:
            judgement[1] = 1
        #下抜けでクローズ
        if low < closeLowLine[-1]:
            judgement[2] = 1
        #上抜けでクローズ
        if high > closeHighLine[-1]:
            judgement[3] = 1
        return judgement

    def getCandlestick(self, number, period):
        """
        number:ローソク足の数．period:ローソク足の期間（文字列で秒数を指定，Ex:1分足なら"60"）．cryptowatchはときどきおかしなデータ（price=0）が含まれるのでそれを除く．
        """
        #ローソク足の時間を指定
        periods = [period]
        #クエリパラメータを指定
        query = {"periods":','.join(periods)}
        #ローソク足取得
        res = \
            json.loads(requests.get("https://api.cryptowat.ch/markets/bitflyer/btcfxjpy/ohlc", params=query).text)[
                "result"]
        # ローソク足のデータを入れる配列．
        data = []
        for i in periods:
            row = res[i]
            length = len(row)
            for column in row[:length - (number + 1):-1]:
                # dataへローソク足データを追加．
                if column[4] != 0:
                    column = column[0:6]
                    data.append(column)
        return data[::-1]


    def fromListToDF(self, candleStick):
        """
        Listのローソク足をpandasデータフレームへ．
        """
        date = [price[0] for price in candleStick]
        priceOpen = [int(price[1]) for price in candleStick]
        priceHigh = [int(price[2]) for price in candleStick]
        priceLow = [int(price[3]) for price in candleStick]
        priceClose = [int(price[4]) for price in candleStick]
        date_datetime = map(datetime.datetime.fromtimestamp, date)
        dti = pd.DatetimeIndex(date_datetime)
        df_candleStick = pd.DataFrame({"open" : priceOpen, "high" : priceHigh, "low": priceLow, "close" : priceClose}, index=dti)
        return df_candleStick

    def processCandleStick(self, candleStick, timeScale):
        """
        1分足データから各時間軸のデータを作成.timeScaleには5T（5分），H（1時間）などの文字列を入れる
        """
        df_candleStick = self.fromListToDF(candleStick)
        processed_candleStick = df_candleStick.resample(timeScale).agg({'open': 'first','high':'max','low': 'min','close': 'last'})
        processed_candleStick = processed_candleStick.dropna()
        return processed_candleStick

    def lineNotify(self, message, fileName=None):
        payload = {'message': message}
        headers = {'Authorization': 'Bearer ' + self.line_notify_token}
        if fileName == None:
            try:
                requests.post(self.line_notify_api, data=payload, headers=headers)
            except:
                pass
        else:
            try:
                files = {"imageFile": open(fileName, "rb")}
                requests.post(self.line_notify_api, data=payload, headers=headers, files = files)
            except:
                pass

    def describePLForNotification(self, pl, df_candleStick):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        close = df_candleStick["close"]
        index = range(len(pl))
        # figure
        fig = plt.figure(figsize=(20,12))
        #for price
        ax = fig.add_subplot(2, 1, 1)
        ax.plot(df_candleStick.index, close)
        ax.set_xlabel('Time')
        # y axis
        ax.set_ylabel('The price[JPY]')
        #for PLcurve
        ax = fig.add_subplot(2, 1, 2)
        # plot
        ax.plot(index, pl, color='b', label='The PL curve')
        ax.plot(index, [0]*len(pl), color='b',)
        # x axis
        ax.set_xlabel('The number of Trade')
        # y axis
        ax.set_ylabel('The estimated Profit/Loss(JPY)')
        # legend and title
        ax.legend(loc='best')
        ax.set_title('The PL curve(Time span:{})'.format(self.candleTerm))
        # save as png
        today = datetime.datetime.now().strftime('%Y%m%d')
        number = "_" + str(len(pl))
        fileName = today + number + ".png"
        plt.savefig(fileName)
        plt.close()
        return fileName
######################
##### MAIN LOGIC #####
######################
    def loop(self,entryTerm, closeTerm, rangeTh, rangeTerm,originalWaitTerm, waitTh,candleTerm=None):
        """
        注文の実行ループを回す関数
        """
        #try:
        #    self.executionsProcess()
        #except:
        #    pass
        ##pubnubが回り始めるまで待つ．
        #time.sleep(30)
        profitPos = 0.0
        lastSide = 0
        okOrder = False
        pos = 0
        pl = []
        pl.append(0)
        lastPositionPrice = 0
        lot = self.lot
        lot = self.calculateLot(self.margin)
        if (lot < self.lot):
            lot = self.lot
        originalLot = self.lot
        waitTerm = 0
        try:
           candleStick = self.getCandlestick(50, "60")
        except:
            print("Unknown error happend when you requested candleStick")
        if candleTerm == None:
            df_candleStick = self.fromListToDF(candleStick)
        else:
            df_candleStick = self.processCandleStick(candleStick, candleTerm)
        entryLowLine, entryHighLine = self.calculateLines(df_candleStick, entryTerm)
        closeLowLine, closeHighLine = self.calculateLines(df_candleStick, closeTerm)
#
        #print("entryLowLine  =" + str(entryLowLine))
        #print("entryHighLine =" + str(entryHighLine))
        #print("closeLowLine  =" + str(closeLowLine))
        #print("closeHighLine =" + str(closeHighLine))
#
        #直近約定件数30件の高値と安値
        #high = max([self.executions[-1-i]["price"] for i in range(60)])
        #low = min([self.executions[-1-i]["price"] for i in range(60)])
        #high = max([candleStick[-1-i][2] for i in range(30)])
        #low = max([candleStick[-1-i][3] for i in range(30)])

        while True:
            #1分ごとに基準ラインを更新
            #if datetime.datetime.now().second < 2 :
            #5分ごとに基準ラインを更新
            if datetime.datetime.now().minute % 5 == 0 and datetime.datetime.now().second == 0:
                print("Renewing candleSticks")
                try:
                    candleStick = self.getCandlestick(50, "60")
                except:
                    print("Unknown error happend when you requested candleStick")
                if candleTerm == None:
                    df_candleStick = self.fromListToDF(candleStick)
                else:
                    df_candleStick = self.processCandleStick(candleStick, candleTerm)
                entryLowLine, entryHighLine = self.calculateLines(df_candleStick, entryTerm)
                closeLowLine, closeHighLine = self.calculateLines(df_candleStick, closeTerm)
#
                #print("entryLowLine  =" + str(entryLowLine))
                #print("entryHighLine =" + str(entryHighLine))
                #print("closeLowLine  =" + str(closeLowLine))
                #print("closeHighLine =" + str(closeHighLine))
#
            #直近約定件数30件の高値と安値
            #high = max([self.executions[-1-i]["price"] for i in range(30)])
            #low = min([self.executions[-1-i]["price"] for i in range(30)])
            high = max([candleStick[-1-i][4] for i in range(30)])
            low = min([candleStick[-1-i][4] for i in range(30)])
#
            #print("high          =" + str(high))
            #print("low           =" + str(low))
            judgement = self.judgeForLoop(high, low, entryHighLine, entryLowLine, closeHighLine, closeLowLine)
            #print("judgement=" + str(judgement[0])+":"+str(judgement[1])+":"+str(judgement[2])+":"+str(judgement[3]))
#
            #現在レンジ相場かどうか．
            isRange = self.isRange(df_candleStick, rangeTerm, rangeTh)

            try :
                ticker = self.api.ticker(product_code=self.product_code)
            except:
                print("Unknown error happend when you requested ticker.")
                time.sleep(1)
            finally:
                pass

            best_ask = ticker["best_ask"]
            best_bid = ticker["best_bid"]
            #ここからエントリー，クローズ処理
            if pos == 0 and not isRange[-1]:
                #ロングエントリー
                if judgement[0]:
                    okOrder = False
                    if (lastSide != 0):
                        if (lastSide == -1):
                            plRange = lastPositionPrice - best_ask
                            profitPos = (plRange * lot)
                            if (profitPos > self.cost*self.lot*self.margin*10*10):
                                logger.info("Long(Short->Long) Entry Profit=" + str(profitPos))
                                okOrder = True
                        elif(lastSide == 1):
                            okOrder = False
                    else:
                        logger.info("First Long Entry Position=" + str(profitPos))
                        okOrder = True
                        lastSide = 1
                    if (okOrder):
                        print(datetime.datetime.now())
                        self.order.market(size=lot, side="BUY")
                        pos += 1
                        message = "Long entry. Lot:{}, Price:{}".format(lot, best_ask)
                        ###self.lineNotify(message)
                        logger.info(message)
                        lastPositionPrice = best_ask
                        time.sleep(10)
                #ショートエントリー
                elif judgement[1]:
                    okOrder = False
                    if (lastSide != 0):
                        if (lastSide == 1):
                            plRange = best_bid - lastPositionPrice
                            profitPos = (plRange * lot)
                            if (profitPos > self.cost*self.lot*self.margin*10*10):
                                logger.info("Long(Long-->Short) Entry Profit=" + str(profitPos))
                                okOrder = True
                        elif(lastSide == -1):
                            okOrder = False
                    else:
                        logger.info("First Short Entry Position=" + str(profitPos))
                        okOrder = True
                        lastSide = -1
                    if (okOrder):
                        print(datetime.datetime.now())
                        self.order.market(size=lot,side="SELL")
                        pos -= 1
                        message = "Short entry. Lot:{}, Price:{}, ".format(lot, best_bid)
                        ###self.lineNotify(message)
                        logger.info(message)
                        lastPositionPrice = best_bid
                        time.sleep(10)

            elif pos == 1:
                #ロングクローズ
                if judgement[2]:
                    # マイナス損益だったらSKIPする。(fxplan.com Add)
                    plRange = lastPositionPrice - best_ask
                    pl.append(pl[-1] + plRange * lot)
                    profitPos = pl[-1]
                    #print("Profit=" + str(profitPos))
                    logger.info("Long Close Profit=" + str(profitPos))
                    if ((profitPos > self.cost*self.lot*self.margin*10*10*10)\
                      or (profitPos < LOSS_CUT)):
                        print(datetime.datetime.now())
                        self.order.market(size=lot,side="SELL")
                        pos -= 1
                        mes = None
                        if (profitPos>0.0): mes = " +Profit"
                        else: mes = " -Loss"
                        message = "bitFlyer Bot(BTCJPY_FX) Long Close Lot:{}, Price:{}, pl:{}, Result:{}".format(lot, best_bid, profitPos, mes)
                        fileName = self.describePLForNotification(pl, df_candleStick)
                        self.lineNotify(message,fileName)
                        logger.info(message)
                        #一定以上の値幅を取った場合，次の10トレードはロットを1/10に落とす．
                        if plRange > waitTh:
                            waitTerm = originalWaitTerm
                            lot = round(originalLot/10,3)
                        elif waitTerm > 0:
                            waitTerm -= 1
                            lot = round(originalLot/10,3)
                        if waitTerm == 0:
                             lot = originalLot
                        lastSide = -1
                        time.sleep(10)
                    else:
                        #print("Skip Long Signal for Loss:" + "{}".format(profitPos))
                        logger.info("Skip Long Signal for Loss:" + "{}".format(profitPos))
                        time.sleep(10)
            elif pos == -1:
                #ショートクローズ
                if judgement[3]:
                    # マイナス損益だったらSKIPする。(fxplan.com Add)
                    plRange = best_bid - lastPositionPrice
                    pl.append(pl[-1] + plRange * lot)
                    profitPos = pl[-1]
                    #print("Profit=" + str(profitPos))
                    #logger.info("Short Close Profit=" + str(profitPos))
                    if ((profitPos > self.cost*self.lot*self.margin*10*10*10)\
                      or (profitPos < LOSS_CUT)):
                        print(datetime.datetime.now())
                        self.order.market(size=lot, side="BUY")
                        pos += 1
                        mes = None
                        if (profitPos>0.0): mes = " +Profit"
                        else: mes = " -Loss"
                        message = "bitFlyer Bot(BTCJPY_FX) Short Close Lot:{}, Price:{}, pl:{}, Result:{}".format(lot, best_ask, profitPos, mes)
                        fileName = self.describePLForNotification(pl, df_candleStick)
                        self.lineNotify(message,fileName)
                        logger.info(message)
                        #一定以上の値幅を取った場合，次の10トレードはロットを1/10に落とす．
                        if plRange > waitTh:
                            waitTerm = originalWaitTerm
                            lot = round(originalLot/10,3)
                        elif waitTerm > 0:
                            waitTerm -= 1
                            lot = round(originalLot/10,3)
                        if waitTerm == 0:
                            lot = originalLot
                        lastSide = 1
                        time.sleep(10)
                    else:
                        #print("Skip Short Signal for Loss:" + "{}".format(profitPos))
                        logger.info("Skip Short Signal for Loss:" + "{}".format(profitPos))
                        time.sleep(10)
#
            #time.sleep(0.5)
            time.sleep(5)
            message = "Waiting for channelbreaking."

            #if datetime.datetime.now().minute % 60 == 0 and datetime.datetime.now().second < 1:
            if datetime.datetime.now().minute % 60 == 0 and datetime.datetime.now().second == 0:
                print(message)
                if(message != "Waiting for channelbreaking."):
                	self.lineNotify(message)
                	#logger.info(message)
#
    def getSpecifiedCandlestick(self,number, period, start_timestamp, end_timestamp):
        """
        number:ローソク足の数．period:ローソク足の期間（文字列で秒数を指定，Ex:1分足なら"60"）．cryptowatchはときどきおかしなデータ（price=0）が含まれるのでそれを除く
        """
        # ローソク足の時間を指定
        periods = [period]
        # クエリパラメータを指定
        query = {"periods": ','.join(periods), "after": str(int(start_timestamp)), "before": str(int(end_timestamp))}
        # ローソク足取得
        try:
            res = json.loads(requests.get("https://api.cryptowat.ch/markets/bitflyer/btcfxjpy/ohlc", params=query).text)
            res = res["result"]
        except:
            print(res)
        # ローソク足のデータを入れる配列．
        data = []
        for i in periods:
            row = res[i]
            length = len(row)
            for column in row[:length - (number + 1):-1]:
                # dataへローソク足データを追加．
                if column[4] != 0:
                    column = column[0:6]
                    data.append(column)
        return data[::-1]


#注文処理をまとめている
class Order:
    def __init__(self):
        self.product_code = "FX_BTC_JPY"
        self.key = "8EyNsgunSKEaEQQ5qs8hq3"
        self.secret = "g98V1NlN7BbdgIbFcSQiCkPUkPDtSYZfWooJDtI0CO8="
        self.api = pybitflyer.API(self.key, self.secret)

    def market(self, side, size, minute_to_expire= None):
        print("Order: Market. Side : {} Lots : {}".format(side, size))
        response = {"status": "internalError in order.py"}
        try:
            response = self.api.sendchildorder(product_code=self.product_code, child_order_type="MARKET", side=side, size=size, minute_to_expire = minute_to_expire)
        except:
            pass
        i=0
        while "status" in response:
            try:
                response = self.api.sendchildorder(product_code=self.product_code, child_order_type="MARKET", side=side, size=size, minute_to_expire = minute_to_expire)
            except:
                pass
            time.sleep(3)
            i=i+1
            if(i>10):
            	break;
        return response

if __name__ == '__main__':
    #とりあえず5分足，5期間安値・高値でエントリー，クローズする設定
    channelBreakOut = ChannelBreakOut()
    channelBreakOut.entryTerm = 5
    channelBreakOut.closeTerm = 5
    channelBreakOut.rangeTh = None
    channelBreakOut.rangeTerm = 15
    channelBreakOut.waitTerm =5
    channelBreakOut.waitTh = 5000
    channelBreakOut.candleTerm = "5T"
    channelBreakOut.cost = 0.1
    channelBreakOut.margin = 15

    #実働
    channelBreakOut.loop(channelBreakOut.entryTerm, channelBreakOut.closeTerm, channelBreakOut.rangeTh, channelBreakOut.rangeTerm, channelBreakOut.waitTerm, channelBreakOut.waitTh)
    