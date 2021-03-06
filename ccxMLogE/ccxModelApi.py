# -*- coding:utf-8 -*-
"""
机器学习平台MLogE的封装接口函数
"""
import pickle
import threading

import flask
import requests
from flask import request
import time
import json
from ccxMLogE.config import f_mdAllconf
from ccxMLogE.inputTransform import f_getCateList, f_ReadData
from ccxMLogE.logModel import ml_infologger, f_stdout2log
from ccxMLogE.outputTransform import f_part2Output, f_type1Output, f_type2Output, f_part2Output4yibu, \
    f_modelPredictOutputType0, f_modelPredictOutputType1
from ccxMLogE.predictModel import predictmodel, f_save_predictRes
from ccxMLogE.preparationData import f_dummyOld, f_splitdata, processData, f_genAllcol, f_saveprocessData
from ccxMLogE.trainModel import f_trainModelMain
from ccxMLogE.varDescSummary import f_mainDesc, f_viewdata

server = flask.Flask(__name__)


@server.route('/ccxModelApi', methods=['post'])
def ccxModelApi():
    try:
        st = time.time()
        # 1.解析数据
        Input = json.loads(request.data.decode())
        reqId = Input.get('reqId')
        type = Input.get('type')
        userPath = Input.get('userPath')
        base = Input.get('base')
        fields = Input.get('fields')

        # 2017-12-20 新增 info日志 用于给用户实时展示东西
        username = userPath.split('/')[-1]
        mllog, logpath = ml_infologger(username, reqId)

        mllog.info('前端请求接口数据%s' % Input)
        # 2.修改用户的超参数配置文件
        flag = f_mdAllconf(userPath)
        # flag 为True说明了 用户目录下有配置文件且路径配置完成
        # 3.数据预处理
        # 解析用户自定义的离散型变量
        cateList = f_getCateList(fields)
        # 读取数据
        rawdata = f_ReadData(base)
        # 数据概览
        datasetInfo = f_viewdata(rawdata, (base['programName'] + str(base['pId'])))

        # 1208 遇到文件多? 的bug 先自己处理一下 后续交由李龙处理
        col0 = rawdata.columns[0]
        rawdata = rawdata.rename(columns={col0: col0.split('?')[-1]})
        if type == 0:
            # print('变量统计')
            # mllog.info('变量分析中')
            # # 数据的描述性分析
            # resdesc = f_mainDesc(rawdata, base['indexName'], base['targetName'], cateList)
            # descout, path3 = f_part2Output(resdesc, userPath, rawdata)
            # res = f_type1Output(reqId, datasetInfo, descout, path3)
            # mllog.info('变量分析结束')
            with server.app_context():
                t = threading.Thread(target=f_threadVarDesc,
                                     args=(rawdata, base, cateList, userPath, reqId, datasetInfo, mllog))
                t.start()
            res = json.dumps({"code": 200, "logPath": logpath, "msg": '变量分析中 请耐心等待'}, ensure_ascii=False)
        elif type == 1:
            # 数据的描述性分析,在计算一遍不是很明智，但是可以想个办法
            # 主要为了对付用户调整了变量的类型之后 需要重新计算的问题
            # 起一个异步线程去跑模型
            with server.app_context():
                t = threading.Thread(target=f_threadModelTrain,
                                     args=(rawdata, base, cateList, reqId, datasetInfo, userPath, mllog, logpath))
                t.start()
            res = json.dumps({"code": 200, "logPath": logpath, "msg": '模型正在运行 请耐心等待'}, ensure_ascii=False)

        mllog.info('请求用时%0.2f s' % (time.time() - st))
        # mllog.info('变量回调内容:正常===\n %s' % res)
        return res
    except Exception as e:
        return json.dumps({"code": 500, "msg": str(e)})


def f_threadVarDesc(rawdata, base, cateList, userPath, reqId, datasetInfo, mllog):
    '''
    将同步的变量分析接口 改为异步
    :param rawdata:
    :param base:
    :param cateList:
    :param userPath:
    :param reqId:
    :param datasetInfo:
    :param mllog:
    :return:
    '''
    try:
        st = time.time()
        print('变量统计')
        mllog.info('变量分析中')
        # 数据的描述性分析
        resdesc = f_mainDesc(rawdata, base['indexName'], base['targetName'], cateList)
        descout, path3 = f_part2Output(resdesc, userPath, rawdata)
        res = f_type1Output(reqId, datasetInfo, descout, path3)
        mllog.info('变量分析结束')
        mllog.info('变量分析总计用时:%0.2f s' % (time.time() - st))

        # 回调变量分析输出接口
        header_dict = {"Content-Type": "application/json"}
        # url = 'http://10.0.5.136:9999/variable/api'  # 开发环境请求接口
        # url = 'http://192.168.100.175:8080/ccx-models/variable/api'  # 线上测试环境请求接口
        url = 'http://127.0.0.1:8081/ccx-models/variable/api'  # 线上生产环境请求接口
        res_ = res.encode('utf-8')
        r = requests.post(url, data=res_, headers=header_dict)
        # print('用时' * 20, (time.time() - st()))
        print(r.text)
        return res
    except Exception as e:
        header_dict = {"Content-Type": "application/json"}
        # url = 'http://10.0.5.136:9999/variable/api'  # 开发环境请求接口
        # url = 'http://192.168.100.175:8080/ccx-models/variable/api'  # 线上测试环境请求接口
        url = 'http://127.0.0.1:8081/ccx-models/variable/api'  # 线上生产环境请求接口
        res = json.dumps({"code": 501, "reqId": reqId, "msg": str(e)}, ensure_ascii=False)
        res_ = res.encode('utf-8')
        r = requests.post(url, data=res_, headers=header_dict)
        # print('用时' * 20, (time.time() - st()))
        print(r.text)
        mllog.info('变量分析回调内容：异常===%s\n' % res)
        return res


def f_threadModelTrain(rawdata, base, cateList, reqId, datasetInfo, userPath, mllog, logPath):
    # 会了前端计时方便 计算错误也要回调
    try:
        mllog.info('模型服务已启动')
        st = time.time()
        mllog.info('变量分析中')
        # dummyList = f_VarTypeClassfiy(rawdata, cateList)
        resdesc = f_stdout2log(logPath, f_mainDesc, rawdata, base['indexName'], base['targetName'], cateList)
        descout, path3 = f_part2Output4yibu(resdesc, userPath)  # path3 即为所有变量的IV值计算
        # res = f_type1Output(reqId, datasetInfo, descout, path3)
        mllog.info('开始跑模型 ' * 5)
        # 模型数据的准备
        dummyList = list(set(resdesc[4]) - set(resdesc[5]))  # 需要one-hot - 多分类
        dummyAfterdf = f_dummyOld(rawdata, dummyList)
        train_path, test_path = f_splitdata(dummyAfterdf, base['targetName'])
        # 模型训练
        modeltype = f_getmodelType(base)
        mllog.info('%s 模型开始训练 ' % modeltype)
        train_path.index = range(len(train_path))  # 必须加 1129 发现的bug
        test_path.index = range(len(test_path))
        repathlist = f_stdout2log(logPath, f_trainModelMain, train_path, test_path, base['indexName'],
                                  base['targetName'], userPath,
                                  modeltype,
                                  base['arithmetic'])
        # 保存模型对象 供后续预测使用 1212
        modelname = modeltype.split('_')[0]
        psd = processData(modelname, dummyList, f_genAllcol(dummyAfterdf), repathlist[1])
        modelPath = f_saveprocessData(psd, reqId, userPath)

        # 模型输出结果
        res = f_type2Output(reqId, datasetInfo, descout, path3, repathlist, rawdata.columns, train_path, test_path,
                            base['targetName'], userPath, resdesc, modelPath)

        mllog.info('=模型运行完毕=' * 5)
        mllog.info('模型训练总计用时:%0.2fs' % (time.time() - st))
        mllog.info('模型结果输出至前端\n\n\n')

        # 回调输出接口
        header_dict = {"Content-Type": "application/json"}
        # url = 'http://10.0.5.136:9999/output/api'  # 开发环境请求接口
        # url = 'http://192.168.100.175:8080/ccx-models/output/api'  # 线上测试环境请求接口
        url = 'http://127.0.0.1:8081/ccx-models/output/api'  # 线上生产环境请求接口
        res_ = res.encode('utf-8')
        r = requests.post(url, data=res_, headers=header_dict)
        # print('用时' * 20, (time.time() - st()))
        print(r.text)
        return res
    except Exception as e:
        header_dict = {"Content-Type": "application/json"}
        # url = 'http://10.0.5.136:9999/output/api'  # 开发环境请求接口
        # url = 'http://192.168.100.175:8080/ccx-models/output/api'  # 线上测试环境请求接口
        url = 'http://127.0.0.1:8081/ccx-models/output/api'  # 线上生产环境请求接口
        res = json.dumps({"code": 502, "reqId": reqId, "msg": str(e)}, ensure_ascii=False)
        res_ = res.encode('utf-8')
        r = requests.post(url, data=res_, headers=header_dict)
        # print('用时' * 20, (time.time() - st()))
        print(r.text)
        mllog.info('模型回调内容：异常===%s\n' % res)
        return res


def f_getmodelType(base):
    '''
    依据前端输入的base信息 判断出用户想要跑的模型类型 12种
    :param base:
    :return:
    '''
    MODELDICT = {'Xgboost': {'demo': 'ccxboost_demo',
                             'speed': 'ccxboost_speed',
                             'accuracy': 'ccxboost_accuracy',
                             'stable': 'ccxboost_stable'
                             },
                 'GBM': {'demo': 'ccxgbm_demo',
                         'speed': 'ccxgbm_speed',
                         'accuracy': 'ccxgbm_accuracy',
                         'stable': 'ccxgbm_stable'},
                 'RF': {'demo': 'ccxrf_demo',
                        'speed': 'ccxrf_speed',
                        'accuracy': 'ccxrf_accuracy',
                        'stable': 'ccxrf_stable'}
                 }
    # base['arithmetic']  # 大的模型方向 Xgboost GBM RF
    # base['modelConf']  # 小的模型参数配置 demo speed accuracy stable
    return MODELDICT[base['arithmetic']][base['modelConf']]


@server.route('/ccxModelApi/predict', methods=['post'])
def ccxModelApiPredict():
    try:
        st = time.time()
        # 1.解析数据
        Input = json.loads(request.data.decode())
        reqId = Input.get('reqId')
        # modelreqId = Input.get('modelreqId') # 留着后期将其处理的更严谨
        modelPath = Input.get('modelPath')
        base = Input.get('base')
        indexName = base['indexName']
        targetName = base['targetName']
        type = Input.get('type')
        if type == 0:
            print('前端请求接口数据', Input)

            # 获取到保存下来的processData 对象

            processData = f_load(modelPath)

            # 读取待预测的数据集
            test = f_ReadData(base)

            # 进行预测
            res = predictmodel(processData, test, indexName)

            # 结果保存
            predictResPath = f_save_predictRes(res, modelPath)

            # 正常情况下 返回结果
            rest = f_modelPredictOutputType0(reqId, predictResPath)
            print('返回无监督预测接口的结果', rest)
            print('预测用时%0.2f s' % (time.time() - st))
            return rest
        elif type == 1:
            print('前端请求接口数据', Input)

            # 获取到保存下来的processData 对象

            processData = f_load(modelPath)

            # 读取待预测的数据集
            test = f_ReadData(base)

            # 进行预测
            res = predictmodel(processData, test, indexName, targetName=targetName)

            # 结果保存
            predictResPath = f_save_predictRes(res, modelPath)
            print('sdcdcdf--bugbugbug', predictResPath)

            # 正常情况下 返回结果
            rest = f_modelPredictOutputType1(reqId, predictResPath, processData.bstmodelpath, test, base)
            print('返回有监督预测接口的结果', rest)
            print('预测用时%0.2f s' % (time.time() - st))
            return rest

    except Exception as e:
        return json.dumps({'code': 503, 'Msg': str(e)}, ensure_ascii=False)


def f_load(modelPath):
    with open(modelPath, 'rb') as f:
        re = pickle.load(f)
    return re


if __name__ == '__main__':
    server.run(debug=True, port=6060, host='0.0.0.0', threaded=True)
