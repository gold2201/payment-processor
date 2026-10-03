# Payment Processor

Асинхронный микросервис обработки платежей. Принимает запрос на оплату, обрабатывает его через эмуляцию платёжного шлюза и уведомляет клиента о результате по webhook.

## Стек

- FastAPI, Pydantic v2
- SQLAlchemy 2.0 (async), asyncpg, PostgreSQL
- RabbitMQ 3.13, FastStream
- Alembic
- Docker, docker-compose
- pytest, ruff, mypy

## Как это работает

1. `POST /api/v1/payments` в **одной транзакции** сохраняет платёж и событие в таблицу `outbox`, затем отвечает `202 Accepted`.
2. **Outbox dispatcher** раз в секунду забирает неотправленные события и публикует их в очередь `payments.new`.
3. **Consumer** получает сообщение, эмулирует шлюз (2–5 с, 90% успех / 10% отказ), записывает статус `succeeded` или `failed` и отправляет webhook.
4. Технические сбои обрабатываются повторами и Dead Letter Queue.

Отказ шлюза (10%) это штатный результат: платёж получает статус `failed`, webhook отправляется как обычно. Повторы и DLQ нужны для технических сбоев: БД, брокер, недоступный webhook.

### Сервисы

| Сервис | Назначение |
|---|---|
| `api` | FastAPI, принимает запросы |
| `outbox-dispatcher` | публикует события из `outbox` в RabbitMQ |
| `consumer` | обрабатывает платежи и отправляет webhook |
| `migrate` | разово применяет миграции Alembic при старте |
| `postgres`, `rabbitmq` | инфраструктура |

### Гарантии доставки

Доставка событий работает по принципу *at-least-once*, дубликаты безопасны, потому что consumer идемпотентен.

**Outbox.** Событие пишется в БД вместе с платежом, поэтому оно не потеряется, даже если брокер недоступен. Статусы события: `pending` → `processing` → `published` (или `failed`). Dispatcher выбирает события через `SELECT ... FOR UPDATE SKIP LOCKED`, поэтому несколько dispatcher'ов не берут одно и то же событие. Зависшие в `processing` более 5 минут события возвращаются в `pending`. Если публикация не удалась, событие откладывается с экспоненциальной задержкой и джиттером (до 10 попыток, максимум 60 с между попытками). После этого оно получает статус `failed` и отправляется в DLQ.

**Идемпотентность.** Заголовок `Idempotency-Key` обязателен, ключ хранится в `payments.idempotency_key` с unique-ограничением. Повторный запрос с тем же ключом возвращает уже созданный платёж и не создаёт ни платёж, ни событие. Гонка двух параллельных запросов решается через `IntegrityError`: проигравший запрос возвращает платёж победителя. Consumer тоже идемпотентен: платёж захватывается атомарным `UPDATE`, а итоговый статус записывается только один раз.

### Retry и DLQ

Есть три независимых уровня повторов.

| Уровень | Что повторяется | Попытки | Задержки | Итог при исчерпании |
|---|---|---|---|---|
| Outbox dispatcher | публикация события в `payments.new` | 10 | экспоненциально от 3 с, максимум 60 с, с джиттером | событие `failed` и сообщение в DLQ |
| Consumer | обработка сообщения | 3 | очередь `payments.new.retry.1` (5 с), затем `payments.new.retry.2` (10 с) | сообщение в DLQ |
| Webhook | HTTP POST на `webhook_url` | 3 | 2 с, затем 4 с | сообщение в DLQ |

Подробности:

- Номер попытки consumer'а хранится в заголовке `x-retry-count`. После 1-й неудачи сообщение уходит в `payments.new.retry.1`, после 2-й в `payments.new.retry.2`, после 3-й в DLQ. Из retry-очередей сообщение по истечении TTL возвращается в `payments.new`.
- Webhook считается доставленным при ответе `2xx`. Любой другой ответ и сетевая ошибка считаются неудачей и повторяются. Если все попытки исчерпаны, в DLQ публикуется сообщение с причиной `webhook_failed: ...`. Статус платежа при этом остаётся итоговым.
- Если платёж без `webhook_url`, webhook пропускается.
- Топология RabbitMQ: exchange `payments` и `payments.dlx`, очереди `payments.new`, `payments.new.retry.1`, `payments.new.retry.2`, `payments.new.dlq`. Основная очередь `payments.new` настроена с `x-dead-letter-exchange`, поэтому отклонённые сообщения попадают в DLQ.

## Запуск через Docker

Нужны Docker и Docker Compose.

```bash
cp .env.example .env
docker compose up --build -d
```

При старте сервис `migrate` применяет миграции (`alembic upgrade head`), затем поднимаются `api`, `consumer` и `outbox-dispatcher`.

| Сервис | Адрес |
|---|---|
| API | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| Health-check (без ключа) | http://localhost:8000/health |
| RabbitMQ Management | http://localhost:15672 (guest / guest) |
| PostgreSQL | localhost:5432 (payments / payments) |

Логи и остановка:

```bash
docker compose logs -f consumer outbox-dispatcher
docker compose down
docker compose down -v
```

## Конфигурация

Настройки читаются из `.env` (шаблон: `.env.example`). Основные переменные:

| Переменная | По умолчанию | Описание |
|---|---|---|
| `API_KEY` | `super-secret-api-key` | значение заголовка `X-API-Key` |
| `POSTGRES_HOST` / `PORT` / `USER` / `PASSWORD` / `DB` | `localhost` / `5432` / `payments` / — / `payments` | подключение к PostgreSQL |
| `RABBITMQ_HOST` / `PORT` / `USER` / `PASSWORD` / `VHOST` | `localhost` / `5672` / `guest` / — / `/` | подключение к RabbitMQ  |
| `MAX_CONSUMER_ATTEMPTS` | `3` | число попыток обработки в consumer'е |
| `PAYMENTS_RETRY_BASE_DELAY_MS` | `5000` | базовая задержка retry-очереди (удваивается на каждом уровне) |
| `WEBHOOK_MAX_ATTEMPTS` | `3` | число попыток отправки webhook |
| `WEBHOOK_BASE_DELAY_SECONDS` | `2` | базовая задержка между попытками webhook (удваивается) |
| `WEBHOOK_TIMEOUT_SECONDS` | `10` | таймаут одного HTTP-запроса webhook |
| `OUTBOX_MAX_ATTEMPTS` | `10` | число попыток публикации события outbox |
| `OUTBOX_BASE_RETRY_DELAY_SECONDS` / `OUTBOX_MAX_RETRY_DELAY_SECONDS` | `3` / `60` | границы экспоненциальной задержки outbox |
| `LOG_LEVEL`, `DEBUG` | `INFO`, `false` | логирование; при `DEBUG=true` включается вывод SQL |

В `docker-compose.yml` для `postgres` и `rabbitmq` используются те же переменные `POSTGRES_*` и `RABBITMQ_*`.

## Миграции

Миграции Alembic лежат в `migrations/versions`. В Docker они применяются автоматически сервисом `migrate`. Вручную:

```bash
poetry run alembic upgrade head
poetry run alembic downgrade -1
poetry run alembic revision --autogenerate -m "описание"
```

Таблицы: `payments` и `outbox`.

## Примеры запросов

Все эндпоинты `/api/v1/*` требуют заголовок `X-API-Key`. Без него вернётся `401`, с неверным значением `403`. Ключ задаётся переменной `API_KEY` в `.env`.

### Создание платежа

```bash
curl -i -X POST http://localhost:8000/api/v1/payments \
  -H "Content-Type: application/json" \
  -H "X-API-Key: super-secret-api-key" \
  -H "Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000" \
  -d '{
    "amount": "100.50",
    "currency": "USD",
    "description": "Order #42",
    "metadata": {"order_id": "42", "source": "web"},
    "webhook_url": "https://webhook.site/<your-id>"
  }'
```

Ответ `202 Accepted`:

```json
{
  "payment_id": "9c3f3b1a-2b8e-4d6a-9c1f-1a2b3c4d5e6f",
  "status": "pending",
  "created_at": "2026-10-01T12:00:00Z"
}
```

Повторите тот же запрос с тем же `Idempotency-Key`: придёт тот же `payment_id`, новых платежей не появится.

### Получение платежа

```bash
curl http://localhost:8000/api/v1/payments/9c3f3b1a-2b8e-4d6a-9c1f-1a2b3c4d5e6f \
  -H "X-API-Key: super-secret-api-key"
```

Через 2–5 секунд статус станет `succeeded` или `failed`:

```json
{
  "payment_id": "9c3f3b1a-2b8e-4d6a-9c1f-1a2b3c4d5e6f",
  "amount": "100.50",
  "currency": "USD",
  "description": "Order #42",
  "metadata": {"order_id": "42", "source": "web"},
  "status": "succeeded",
  "webhook_url": "https://webhook.site/<your-id>",
  "created_at": "2026-10-01T12:00:00Z",
  "processed_at": "2026-10-01T12:00:04Z"
}
```

Несуществующий `payment_id` даёт `404`, невалидный UUID `422`.

### Webhook

На `webhook_url` уходит `POST` с телом:

```json
{
  "payment_id": "9c3f3b1a-2b8e-4d6a-9c1f-1a2b3c4d5e6f",
  "status": "succeeded",
  "amount": "100.50",
  "currency": "USD",
  "processed_at": "2026-10-01T12:00:04+00:00"
}
```

Для проверки удобно взять временный адрес на https://webhook.site.

## Как проверить DLQ

Создайте платёж с недоступным webhook (внутри контейнера `localhost` это сам контейнер, поэтому соединение будет отклонено):

```bash
curl -X POST http://localhost:8000/api/v1/payments \
  -H "Content-Type: application/json" \
  -H "X-API-Key: super-secret-api-key" \
  -H "Idempotency-Key: dlq-demo-1" \
  -d '{"amount": "10.00", "currency": "RUB", "webhook_url": "http://localhost:9999/hook"}'
```

Consumer сделает 3 попытки отправить webhook (паузы 2 и 4 секунды), после чего сообщение появится в очереди `payments.new.dlq`. Посмотреть его можно в RabbitMQ UI: **Queues → payments.new.dlq → Get messages**. В теле будут `payment_id`, `reason` (`webhook_failed: ...`) и `payload`. Сам платёж при этом получит итоговый статус (`succeeded` или `failed`).

## Локальная разработка

Нужны Python 3.13 и Poetry. PostgreSQL и RabbitMQ можно поднять из compose:

```bash
poetry install
docker compose up -d postgres rabbitmq

poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload
poetry run python -m app.workers.consumer
poetry run python -m app.workers.outbox_dispatcher
```

Тесты и проверки:

```bash
poetry run pytest
poetry run ruff check .
poetry run mypy app
```

Тесты используют моки. Они покрывают API (аутентификация, валидация, коды ответов), идемпотентность, outbox, процессинг, retry и DLQ в consumer'е, retry webhook. Сквозной сценарий с реальными PostgreSQL и RabbitMQ проверяется вручную по разделам выше.

## Структура проекта

```
app/
  api/            роутеры FastAPI, проверка X-API-Key
  broker/         топология RabbitMQ, публикация сообщений
  common/         enum'ы, backoff, разбор заголовков
  core/           настройки, логирование
  db/             сессия, UnitOfWork
  dtos/           DTO и мапперы
  models/         SQLAlchemy-модели (payments, outbox)
  repositories/   доступ к данным
  schemas/        Pydantic-схемы API
  services/       бизнес-логика: платежи, outbox, процессинг, webhook
  workers/        consumer и outbox dispatcher
migrations/       Alembic
tests/            unit- и API-тесты
```
