# YuMi 知识库人工审核稿

40 条双语问答草稿，未核实为本店真实事实，未录入生产。“建议政策”是供你选择的经营方案；“现场填写”须补全真实信息。删除不适用条目后，中英文同步修改。

配套 JSON 使用 KnowledgeEntry 字段，全部 `is_enabled=false`，不含数据库主键。它是待录入数据，不代表已有批量导入接口。当前后台 `KnowledgeAdminService.create` 默认启用，所以在后台新建前先审核完毕。

一条记录只回答一个主要问题；标题和关键词帮助检索，事实、费用和例外条件写在答案内。地址、距离、房间设施不编造；实时房价和房态不写静态承诺；通用知识不存门锁密码和客人资料。

## 入住与行李

| 编号 | 状态/审核点 | 分类 | 中文问题 | 中文答案草稿 | English question | English answer draft | 检索关键词 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| K001 | 建议政策：确认15点 | 入住退房 | 几点可以入住？ | 入住时间为当日15:00后。需要提前入住，请提前告知预计到店时间，由管家确认能否安排。 | When can I check in? | Check-in starts at 3 PM. For early check-in, please share your arrival time and wait for confirmation from the host. | 入住,提前入住,到店,check in,early arrival |
| K002 | 建议政策：确认12点 | 入住退房 | 几点需要退房？ | 退房时间为离店当日12:00前。延迟退房需提前申请，是否可安排及费用由管家确认。 | What time is check-out? | Check-out is before noon. Late check-out requires advance confirmation of availability and any fee. | 退房,晚退,延迟退房,check out,late checkout |
| K003 | 建议流程：确认夜间接待 | 入住退房 | 深夜或凌晨到店可以入住吗？ | 请提前告知预计到达时间，确认登记和入门指引后再到店。入住日期仍以订单为准，凌晨到店不代表可以提前入住。 | Can I arrive late at night? | Tell the host your expected arrival time and confirm registration and access instructions. Your booking dates still apply; arriving after midnight does not grant early check-in. | 晚到,深夜,凌晨入住,late arrival |
| K004 | 建议流程：核对登记渠道 | 入住退房 | 入住前需要提供哪些信息？ | 请准备订单编号、预计到店时间和实际入住人数，按管家指引完成登记。身份资料通过指定渠道提交，不要发到公共群聊。 | What information is needed before arrival? | Prepare your booking reference, arrival time and guest count. Follow the host's registration instructions and send identity details only through the designated private channel. | 入住登记,身份证,资料,registration,ID |
| K005 | 建议政策：核对各房人数 | 入住退房 | 可以临时增加入住人数吗？ | 入住人数需符合订单和房间核定人数。临时加人请先联系管家，确认是否可以安排及是否收费，不要直接留宿。 | Can I add an overnight guest? | The guest count must comply with the booking and room occupancy limit. Ask the host to confirm availability and any charge before adding an overnight guest. | 加人,多住一人,朋友留宿,extra guest |
| K006 | 建议流程：确认钥匙归还 | 入住退房 | 退房时需要做什么？ | 请检查随身物品，关闭空调和不再使用的电器，关好门窗，并告知管家已离店。如有实体钥匙，请按入住指引归还。 | What should I do at check-out? | Check your belongings, turn off the air conditioning and unused appliances, close doors and windows, and tell the host you have left. Return physical keys as instructed. | 退房流程,离店,归还钥匙,checkout procedure |
| K007 | 建议流程：不保证可寄存 | 行李 | 入住前可以寄存行李吗？ | 请提前联系管家确认是否可寄存、接收时间和地点。贵重物品、证件和现金请随身保管，未经确认不要把行李留在公共区域。 | Can I store bags before check-in? | Confirm storage availability, hours and location with the host first. Keep valuables, documents and cash with you. Do not leave bags in shared areas without confirmation. | 寄存行李,先放箱子,luggage storage |
| K008 | 建议流程：确认取件安排 | 行李 | 退房后可以寄存行李吗？ | 请提前确认能否安排及取件时间、地点。寄存行李不包含继续使用客房，也不自动延长退房时间。 | Can I store bags after check-out? | Confirm availability and collection arrangements in advance. Luggage storage does not include continued room use or extend check-out time. | 退房寄存,取行李,after checkout luggage |

## 门锁与网络

| 编号 | 状态/审核点 | 分类 | 中文问题 | 中文答案草稿 | English question | English answer draft | 检索关键词 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| K009 | 建议流程：确认发放渠道 | 门锁 | 房门密码在哪里获取？ | 管家通过订单对应的联系渠道提供入门指引。没有收到时，请联系管家核对订单和入住身份。不要向非入住人员转发门锁密码。 | How do I get the door code? | The host sends access instructions through your booking contact channel. If they are missing, contact the host to verify your booking and identity. Do not share codes with non-guests. | 开门密码,门锁密码,door code,access |
| K010 | 建议流程：核对门锁操作 | 门锁 | 门锁打不开或密码错误怎么办？ | 请先确认房间及入住日期是否正确，再按入门指引重试。仍无法开门时，向管家提供房间及错误提示，不要强行开锁或拆卸。 | What if the door code does not work? | Check the room and booking dates, then retry as instructed. If it still fails, tell the host your room and the error message. Do not force or dismantle the lock. | 打不开门,密码无效,locked out,invalid code |
| K011 | 现场填写：信息卡位置 | 网络 | Wi-Fi名称和密码在哪里？ | Wi-Fi信息见【待填写：信息卡位置或指定查询渠道】。请使用对应房间的网络信息；找不到时联系管家。 | Where are the Wi-Fi details? | Find the Wi-Fi details at [TO CONFIRM: information card location or designated channel]. Use the details for your room and ask the host if you cannot find them. | WiFi,无线网,网络密码,wifi,internet |
| K012 | 建议操作：核对特殊认证 | 网络 | Wi-Fi连不上怎么办？ | 请核对网络名称和密码，关闭再开启手机Wi-Fi后重试。仍有问题时，将网络名称和错误提示发给管家，不要恢复路由器出厂设置。 | What if Wi-Fi will not connect? | Check the network name and password, then switch your device's Wi-Fi off and on. If it still fails, send the network name and error to the host. Do not factory-reset the router. | 断网,连不上网,wifi not working |

## 停车与早餐

| 编号 | 状态/审核点 | 分类 | 中文问题 | 中文答案草稿 | English question | English answer draft | 检索关键词 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| K013 | 现场填写：车位归属及位置 | 停车 | 开车到店停在哪里？ | 停车安排为【待填写：本店车位或外部公共停车场】。停车位置和入口为【待填写】。请按停车场规则停放，不占用消防通道或他人车位。 | Where can I park? | Parking is [TO CONFIRM: property spaces or an external public car park]. The location and entrance are [TO CONFIRM]. Follow parking rules and keep emergency access and other people's spaces clear. | 停车,泊车,车位,parking,car park |
| K014 | 现场填写：费率和收费主体 | 停车 | 停车免费吗？怎么收费？ | 停车收费规则为【待填写：免费或费率、计费方式、收费主体】。外部停车场按其公示结算，不默认包含在房费内。 | Is parking free? | Parking charges are [TO CONFIRM: free or the rate, charging method and operator]. External parking follows the operator's posted rates and is not automatically included in the room rate. | 停车费,免费停车,parking fee,free parking |
| K015 | 建议流程：核对预约能力 | 停车 | 可以预留停车位吗？ | 请提前向管家确认是否提供预约及是否有可用车位。未收到明确确认，不代表已经预留成功。 | Can I reserve parking? | Ask the host whether reservations are offered and a space is available. A space is not reserved until explicitly confirmed. | 预约车位,预留停车,reserve parking |
| K016 | 现场填写：含早套餐范围 | 早餐 | 房费包含早餐吗？ | 本店早餐政策为【待填写：是否提供、是否含在房费及适用套餐】。附近早餐店是外部商户，不等于本店提供或房费包含早餐。 | Is breakfast included? | The breakfast policy is [TO CONFIRM: whether provided, included, and for which packages]. Nearby shops are independent businesses and do not mean breakfast is provided or included by the property. | 早餐,包早,房费含早,breakfast |
| K017 | 现场填写：真实商户及位置 | 周边餐饮 | 附近哪里可以买早餐？ | 附近早餐选择有【待填写：已核实的商户名称和位置】。这些是外部商户，营业时间和价格以商户当日信息为准，需自行购买。 | Where can I buy breakfast nearby? | Nearby options include [TO CONFIRM: verified business names and locations]. These are independent businesses; check their current hours and prices and pay them directly. | 附近早餐,周边早饭,nearby breakfast |

## 洗衣

| 编号 | 状态/审核点 | 分类 | 中文问题 | 中文答案草稿 | English question | English answer draft | 检索关键词 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| K018 | 现场填写：设备和适用房间 | 洗衣 | 民宿有洗衣机吗？ | 洗衣设施为【待填写：是否提供、公共或房内、位置和适用房间】。请按设备说明使用，特殊面料按衣物洗护标签处理。 | Is a washing machine available? | Laundry facilities are [TO CONFIRM: availability, shared or in-room, location and eligible rooms]. Follow the machine instructions and clothing care labels. | 洗衣机,洗衣服,laundry,washing machine |
| K019 | 现场填写：完整收费规则 | 洗衣 | 洗衣机可以免费使用吗？ | 洗衣收费规则为【待填写：免费或每次费用、支付方式及例外条件】。请按确认后的规则使用洗衣机。 | Is use of the washing machine free? | Laundry charges are [TO CONFIRM: free use or per-cycle fees, payment method and exceptions]. Follow the confirmed charging rules. | 免费洗衣,洗衣收费,laundry fee,free washing |
| K020 | 现场填写：开放时段 | 洗衣 | 洗衣区几点可以使用？ | 洗衣区开放时间为【待填写：起止时间】。请在允许时段使用，完成后及时取走衣物并保持整洁。 | What are the laundry hours? | Laundry hours are [TO CONFIRM: opening and closing times]. Use the facilities during those hours, collect clothes promptly and leave the area clean. | 洗衣时间,晚上洗衣,laundry hours |

## 客房设施与通行

| 编号 | 状态/审核点 | 分类 | 中文问题 | 中文答案草稿 | English question | English answer draft | 检索关键词 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| K021 | 现场填写：空调配置和遥控器 | 客房设施 | 房间有空调吗？遥控器在哪里？ | 【待填写：房间或房型】的空调配置为【待填写】，遥控器位于【待填写】。使用空调时请关好门窗，不同房间配置以对应说明为准。 | Does the room have air conditioning? | Air conditioning in [TO CONFIRM: room or room type] is [TO CONFIRM], with the remote at [TO CONFIRM]. Close doors and windows while using it. Facilities may vary by room. | 空调,制冷,制热,遥控器,air conditioning |
| K022 | 现场填写：逐房电器清单 | 客房设施 | 房间有吹风机、冰箱和电水壶吗？ | 【待填写：房间或房型】提供【待填写：核实后的电器清单及位置】。未列出的设备请向管家确认，不能默认所有房间配置相同。 | Which appliances are in the room? | [TO CONFIRM: room or room type] provides [TO CONFIRM: verified appliances and locations]. Ask the host about items not listed; rooms may have different facilities. | 吹风机,冰箱,水壶,hair dryer,fridge,kettle |
| K023 | 现场填写：厨房使用范围 | 客房设施 | 可以做饭或使用厨房吗？ | 厨房使用政策为【待填写：是否提供、位置、适用房间及允许的烹饪方式】。未确认可使用的设备和区域，请勿自行开火或接入大功率厨具。 | Can I cook or use a kitchen? | Kitchen access is [TO CONFIRM: availability, location, eligible rooms and permitted cooking]. Do not cook or connect high-power cooking appliances without confirmation that the facilities are available for your use. | 厨房,做饭,开火,kitchen,cooking |
| K024 | 建议流程：无设备则改为不提供 | 客房设施 | 可以加床或提供婴儿床吗？ | 请提前向管家申请，是否提供取决于房间条件和实际库存。类型、费用和适用年龄须确认后安排，申请不代表预订成功。 | Can I request an extra bed or cot? | Ask the host in advance. Availability depends on room conditions and actual stock. The type, fee and suitable age range require confirmation; a request is not a confirmed reservation. | 加床,婴儿床,儿童床,extra bed,cot,crib |
| K025 | 现场填写：楼层及台阶 | 通行 | 民宿有电梯吗？需要爬楼吗？ | 【待填写：房间或楼栋】位于【待填写：楼层】，电梯及仍需步行的台阶情况为【待填写】。行动不便或携带大件行李，请在预订前确认具体路线。 | Is there a lift or stairs? | [TO CONFIRM: room or building] is on [TO CONFIRM: floor]. Lift access and any remaining steps are [TO CONFIRM]. Confirm the route before booking if you have mobility needs or large luggage. | 电梯,楼梯,几楼,无障碍,lift,elevator,stairs |

## 住宿规则

| 编号 | 状态/审核点 | 分类 | 中文问题 | 中文答案草稿 | English question | English answer draft | 检索关键词 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| K026 | 建议政策：暂不接待宠物，须人工确认 | 宠物 | 可以带宠物入住吗？ | 本店暂不接待宠物入住。如订单已有经管家明确确认的特殊安排，请以该订单确认内容为准，未确认前不要携带宠物到店。 | Are pets allowed? | Pets are not accepted under this policy. If the host has explicitly confirmed a special arrangement for your booking, follow that confirmation. Do not arrive with a pet without confirmation. | 宠物,带猫,带狗,pet,dog,cat |
| K027 | 建议政策：客房禁烟 | 吸烟 | 房间里可以抽烟或使用电子烟吗？ | 客房内禁止吸烟和使用电子烟。需要吸烟时，请向管家确认允许的室外区域，妥善处理烟头，不在楼道或消防通道吸烟。 | Can I smoke or vape in the room? | Smoking and vaping are not permitted in guest rooms. Ask the host about permitted outdoor areas and dispose of cigarette ends safely. Do not smoke in corridors or emergency exits. | 抽烟,吸烟,电子烟,禁烟,smoking,vaping |
| K028 | 建议政策：确认22点至8点 | 安静时段 | 晚上几点后需要保持安静？ | 每日22:00至次日08:00为安静时段，请降低说话和设备音量，避免公共区域聚集喧哗。其他时段也请尊重邻居和其他住客。 | What are the quiet hours? | Quiet hours are 10 PM to 8 AM. Keep voices and devices low and avoid noisy gatherings in shared areas. Respect neighbours and other guests at all times. | 安静,噪音,吵闹,quiet hours,noise |
| K029 | 建议政策：访客须确认 | 访客 | 朋友可以来房间做客或留宿吗？ | 访客请提前向管家确认规则，不得将未登记访客直接留宿。聚会、拍摄或多人活动需事先确认，不应影响其他住客和邻居。 | Can friends visit or stay overnight? | Confirm visitor rules with the host first. Unregistered visitors must not stay overnight. Gatherings, filming and group activities require prior confirmation and must not disturb others. | 访客,朋友来玩,聚会,留宿,visitors,party |

## 清洁与服务

| 编号 | 状态/审核点 | 分类 | 中文问题 | 中文答案草稿 | English question | English answer draft | 检索关键词 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| K030 | 现场填写：频率及收费 | 清洁 | 每天会打扫房间和更换床品吗？ | 住宿期间的清洁与床品更换安排为【待填写：频率、预约方式、收费规则】。额外服务请联系管家确认时间和费用，不默认承诺当天完成。 | Is daily cleaning and linen replacement included? | Cleaning and linen changes are [TO CONFIRM: frequency, booking method and fees]. Confirm the timing and cost of additional service with the host; same-day completion is not automatically guaranteed. | 打扫,清洁,换床单,床品,housekeeping,linen |
| K031 | 建议流程：核对补充清单 | 清洁 | 毛巾或洗漱用品不够怎么办？ | 请告诉管家房间、用品名称和数量。是否可以补充、是否收费及送达方式由管家确认，不承诺固定送达时间。 | Can I request more towels or toiletries? | Tell the host your room, the items and quantities needed. Availability, any charge and delivery arrangements require confirmation; a fixed delivery time is not promised. | 毛巾,纸巾,牙刷,洗漱用品,towels,toiletries |
| K032 | 现场填写：垃圾投放位置 | 清洁 | 垃圾应该放在哪里？ | 请将垃圾密封后放到【待填写：指定位置】，按现场标识分类。不要将垃圾留在楼道或消防通道，也不要把湿巾或杂物投入马桶。 | Where should I put rubbish? | Seal rubbish bags and take them to [TO CONFIRM: designated location], following sorting signs. Keep corridors and emergency exits clear, and do not flush wet wipes or other items. | 垃圾,垃圾桶,扔垃圾,rubbish,trash |
| K033 | 建议流程：不承诺修复时间 | 维修 | 房间设施坏了怎么办？ | 请向管家说明房间、故障设备、异常现象及是否影响使用，方便时提供照片。不要自行拆修，处理方式和时间由管家确认。 | How do I report a broken facility? | Tell the host your room, the affected item, what is happening and how it affects use. Photos can help. Do not dismantle or repair it yourself; the host will confirm the response and timing. | 坏了,报修,设备故障,维修,broken,maintenance |
| K034 | 建议流程：核对寄送规则 | 遗失物品 | 东西落在房间里怎么办？ | 请尽快提供入住日期、房间和物品描述。确认找到后再安排领取或寄送，寄送方式和运费需确认；未找到前不能保证找回。 | What if I leave something behind? | Contact the host with your stay dates, room and an item description. Collection or shipping can be arranged after it is found, with shipping method and cost agreed. Recovery is not guaranteed. | 遗失物品,落东西,忘拿,lost property,left behind |

## 订单与交通

| 编号 | 状态/审核点 | 分类 | 中文问题 | 中文答案草稿 | English question | English answer draft | 检索关键词 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| K035 | 流程边界：只查实时结果 | 预订 | 今晚有房吗？多少钱？ | 房态和价格需按入住日期、离店日期、人数及房型实时查询。历史房价不代表当前可订价格，最终以实时预订结果及订单确认内容为准。 | Are rooms available tonight and what is the price? | Availability and prices require a live check for your dates, guest count and room type. Historical rates are not current offers; use the live booking result and confirmed booking details. | 空房,今晚价格,房价,可订,availability,room rate |
| K036 | 建议流程：续住重新确认 | 预订 | 可以续住吗？价格一样吗？ | 续住需重新核对后续日期的房态和价格，不默认保留原房间或沿用原价格。请提前联系管家，收到确认后再安排。 | Can I extend my stay at the same rate? | An extension requires a new availability and price check. The same room or rate is not guaranteed. Contact the host in advance and wait for confirmation. | 续住,多住一晚,延长住宿,extend stay |
| K037 | 流程边界：不预设退款承诺 | 订单变更 | 取消、改期或退款怎么办理？ | 请提供订单编号和希望变更的事项，管家将结合预订渠道和适用规则核实。退款金额、改期费用及是否可以办理须确认，不直接承诺。 | How do I cancel, change dates or request a refund? | Provide your booking reference and requested change. The host will check the booking channel and applicable terms. Eligibility, refund amounts and change fees require confirmation. | 取消,改期,退款,退订,cancellation,refund |
| K038 | 建议流程：确认开票主体 | 发票 | 住宿发票怎么申请？ | 请先联系管家确认应向民宿还是预订平台申请，以及可开项目和金额。确认后通过指定渠道提交抬头、税号和接收方式。 | How can I request an invoice? | Ask the host whether to request the invoice from the property or booking platform, and confirm eligible items and amounts. Submit billing details through the designated channel after confirmation. | 发票,报销,开票,invoice,receipt |
| K039 | 现场填写：真实地址与入口 | 地址交通 | 民宿地址在哪里？怎么导航？ | 请导航至【待填写：准确导航名称及详细地址】。实际入口位于【待填写：入口特征、楼栋或楼层】；导航点位与入口不同的，以管家提供的到店路线为准。 | What is the address and where is the entrance? | Navigate to [TO CONFIRM: exact map listing and address]. The entrance is [TO CONFIRM: features, building or floor]. If the map pin differs, follow the host's access directions. | 地址,导航,位置,入口,address,directions |
| K040 | 现场填写：站名出口及实测距离 | 地址交通 | 最近的地铁站和出口在哪里？ | 建议到店使用【待填写：地铁线路、站名及出口】。从该出口步行至民宿约【待填写：实测距离或时间】，主要路线为【待填写】。 | Which metro station and exit should I use? | Use [TO CONFIRM: metro line, station and exit]. The walk from that exit to the property is approximately [TO CONFIRM: measured distance or time], via [TO CONFIRM: route]. | 地铁,出口,步行路线,metro,subway,distance |

## 审核时优先处理

先补 K039 地址、K040 路线、K013—014 停车、K016 早餐、K018—020 洗衣、K021—025 房间设施。对不存在的服务写清“不提供”，不要留下空白承诺。K001、K002、K026—029 的时间或政策是拟定值，须确认后才能启用。

审核动作：保留并核实 / 修改双语 / 删除。审核完成后才将对应条目启用；不要求一次审核完所有条目。
