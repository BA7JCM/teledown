import os
import traceback
from asyncio import CancelledError
from datetime import datetime

from telethon import TelegramClient
from telethon.errors import FileReferenceExpiredError
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto, DocumentAttributeSticker

from tools.tool import (get_file_name, get_history_message, get_chat_id, match_wildcard,
                        parse_user_ids, UserAbort)
from tools.tqdm import TqdmUpTo


# TODO 或许能加快下载速度的方法（https://gist.github.com/painor/7e74de80ae0c819d3e9abcf9989a8dd6）


def _remove_partial(download_path: str) -> None:
    """删除未完成的临时文件，避免残留 .downloading 碎片。"""
    if os.path.exists(download_path):
        os.remove(download_path)


def file_exist(file_path: str, file_size: int) -> tuple[bool, str]:
    """检查文件是否存在（文件名和大小都相等），若存在重名文件则自动加序号。"""
    i = 2
    file_name, file_type = os.path.splitext(file_path)
    temp = file_path
    while os.path.exists(temp):
        if os.path.getsize(temp) == file_size:
            return True, temp
        temp = f'{file_name}({i}){file_type}'
        i += 1
    return False, temp


def get_file_suffix(message) -> list[str]:
    mime_type = 'unknown/unknown'
    if hasattr(message.media, 'document'):
        mime_type = message.media.document.mime_type
    elif hasattr(message.media, 'photo'):
        mime_type = 'image/jpg'
    return mime_type.split('/')


async def download_file(client: TelegramClient, channel_title, channel_id, message, prefix=None):
    message_time = message.date
    formatted_time = datetime.strftime(message_time, '%Y_%m')

    file_name = get_file_name(message)
    if not match_wildcard(prefix, file_name):
        return
    file_path = os.path.join(os.environ.get("save_path", ""), f'{channel_title}-{channel_id}', file_name)
    file_size = message.file.size
    ret, file_path = file_exist(file_path, file_size)
    if ret:
        print(f"媒体已存在：{file_path}")
        return

    download_path = file_path + '.downloading'
    print(f"开始下载：{file_name}")

    for attempt in range(2):  # 最多尝试 2 次
        try:
            with TqdmUpTo(total=file_size, bar_format=TqdmUpTo.bar_format, desc=file_name[:10]) as bar:
                await message.download_media(download_path, progress_callback=bar.update_to)
            os.rename(download_path, file_path)
            return
        except CancelledError:
            print("取消下载")
            _remove_partial(download_path)
            raise UserAbort("下载已被取消")
        except FileReferenceExpiredError:
            # 文档的 file_reference 过期已由 Telethon 内部自愈：_DirectDownloadIter._request()
            # 会捕获 FilerefUpgradeNeededError / FileReferenceExpiredError，重新拉取消息后
            # 从当前分片继续，无需在此重复处理。
            # 照片走的是 InputPhotoFileLocation，不适用该自愈路径，才需要本地换一次新引用重试。
            if attempt == 1 or not isinstance(message.media, MessageMediaPhoto):
                print('文件引用已过期，跳过该文件')
                _remove_partial(download_path)
                return
            print('文件引用已过期，重新获取消息后重试')
            try:
                channel_data = await client.get_entity(int(channel_id))
                new_message = await client.get_messages(channel_data, ids=message.id)
            except Exception:
                print('重新获取消息失败，跳过该文件')
                traceback.print_exc()
                _remove_partial(download_path)
                return
            if new_message is None:
                print('消息已不存在，跳过该文件')
                _remove_partial(download_path)
                return
            message = new_message
        except KeyboardInterrupt:
            print("用户中断下载")
            _remove_partial(download_path)
            raise
        except Exception as e:
            print(f"下载出错 {e.__class__.__name__}: {e}")
            traceback.print_exc()
            _remove_partial(download_path)
            return


async def down_group(client: TelegramClient, chat_id, plus_func: str, from_user, prefix):
    chat_id = await get_chat_id(client, chat_id)
    target_user_ids = await parse_user_ids(client, from_user)
    channel_title, messages = await get_history_message(client, chat_id, plus_func, from_user_ids=target_user_ids)
    async for message in messages:
        if message is None:
            print('慢了一步，消息已被删除')
            continue

        if not isinstance(message.media, (MessageMediaDocument, MessageMediaPhoto)):
            continue

        if isinstance(message.media, MessageMediaDocument) and any(
                isinstance(attr, DocumentAttributeSticker) for attr in message.media.document.attributes):
            continue

        await download_file(
            client=client,
            channel_title=channel_title,
            channel_id=chat_id,
            message=message,
            prefix=prefix
        )
    print(channel_title, '全部下载完成')