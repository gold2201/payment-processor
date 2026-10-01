# Payment Processor

Асинхронный микросервис обработки платежей. Принимает запрос на оплату, обрабатывает его через эмуляцию платёжного шлюза и уведомляет клиента о результате по webhook.

## Стек

- FastAPI, Pydantic v2
- SQLAlchemy 2.0, asyncpg, PostgreSQL
- RabbitMQ 3.13
- Alembic
- Docker, docker-compose
- pytest, ruff, mypy

## Как это работает

1. `POST /api/v1/payments` в **одной транзакции** сохраняет платёж и событие в таблицу `outbox`, затем отвечает `202 Accepted`.
2. **Outbox dispatcher** раз в секунду забирает неотправленные события и публикует их в очередь `payments.new`.
3. **Consumer** получает сообщение, эмулирует шлюз (2–5 с, 90% успех / 10% отказ), записывает статус `succeeded` или `failed` и отправляет webhook.
4. При сбое обработки сообщение уходит на повтор с растущей задержкой, после 3-й неудачной попытки попадает в Dead Letter Queue.

Отказ шлюза (10%) это штатный результат: платёж получает статус `failed`, webhook отправляется как обычно. Повторы и DLQ относятся только к техническим сбоям (БД, брокер, недоступный webhook).

### Топология RabbitMQ

```
payments (direct) --payments.new--> payments.new --(nack)--> payments.dlx --> payments.new.dlq

payments.dlx --payments.new.retry.1--> payments.new.retry.1 (TTL 5 с)  --+
payments.dlx --payments.new.retry.2--> payments.new.retry.2 (TTL 10 с) --+--> payments --> payments.new
```

- `payments.new`: основная очередь. Любое отклонённое сообщение (`nack` без requeue) попадает в DLQ через `payments.dlx`.
- `payments.new.retry.N`: очереди задержки. У них нет consumer'ов, сообщение лежит до истечения TTL и возвращается в `payments.new`.
- `payments.new.dlq`: сообщения, не обработанные за 3 попытки.

### Гарантии доставки

**Outbox.** Событие пишется в БД вместе с платежом, поэтому оно не потеряется, даже если брокер недоступен. Dispatcher выбирает события через `SELECT ... FOR UPDATE SKIP LOCKED`. Если публикация не удалась, событие откладывается с экспоненциальной задержкой и джиттером. После этого оно получает статус `failed` и отправляется в DLQ. Доставка по принципу *at-least-once*.

**Идемпотентность.** Заголовок `Idempotency-Key` обязателен, ключ хранится в `payments.idempotency_key` с unique-ограничением. Повторный запрос с тем же ключом возвращает уже созданный платёж и не создаёт ни платёж, ни событие. Гонка двух параллельных запросов решается через `IntegrityError`: проигравший запрос возвращает платёж победителя. Consumer тоже идемпотентен.

**Retry consumer'а.** 3 попытки обработки. После 1-й неудачи сообщение уходит в `payments.new.retry.1`, после 2-й в `payments.new.retry.2`, после 3-й в DLQ. Номер попытки хранится в заголовке `x-retry-count`.

## Запуск через Docker

Нужны Docker и Docker Compose.

```bash
cp .env.example .env
docker compose up --build -d
```

При старте автоматически применяются миграции, затем поднимаются `api`, `consumer` и `outbox-dispatcher`.

| Сервис | Адрес |
|---|---|
| API | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| RabbitMQ Management | http://localhost:15672 (guest / guest) |
| PostgreSQL | localhost:5432 (payments / payments) |

Логи и остановка:

```bash
docker compose logs -f consumer outbox-dispatcher
docker compose down
docker compose down -v
```

## Примеры запросов

Ключ API задаётся переменной `API_KEY` в `.env`.

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

Consumer сделает 3 попытки отправить webhook (паузы 2 и 4 секунды), после чего сообщение появится в очереди `payments.new.dlq`. Посмотреть его можно в RabbitMQ UI: **Queues → payments.new.dlq → Get messages**. В теле будут `payment_id`, `reason` (`webhook_failed: ...`) и `payload`.


## Локальная разработка

Нужны Python 3.13 и Poetry. PostgreSQL и RabbitMQ можно поднять из compose:

```bash
poetry install
docker compose up -d postgres rabbitmq

poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload
poetry run python -m app.workers.consumer          # в отдельном терминале
poetry run python -m app.workers.outbox_dispatcher # в отдельном терминале
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
