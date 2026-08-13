local M = {}

local MAX_INPUT_LENGTH = 64
local MAX_CANDIDATES = 50
local MAX_CONTEXT_BYTES = 8192
local FIRST_PAGE_TIMEOUT_SECONDS = 0.10
local BRIDGE_ROOT = os.getenv("LOCALAPPDATA") .. "\\NeuralWeasel\\Bridge"
local request_counter = 0

local function is_safe_pinyin(input)
    return #input > 0
        and #input <= MAX_INPUT_LENGTH
        and input:match("^[A-Za-z']+$") ~= nil
end

local BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

local function base64_encode(value)
    return ((value:gsub('.', function(character)
        local byte = character:byte()
        local bits = ""
        for position = 8, 1, -1 do
            bits = bits .. (byte % 2 ^ position - byte % 2 ^ (position - 1) > 0 and "1" or "0")
        end
        return bits
    end) .. "0000"):gsub('%d%d%d?%d?%d?%d?', function(chunk)
        if #chunk < 6 then
            return ""
        end
        local index = 0
        for position = 1, 6 do
            if chunk:sub(position, position) == "1" then
                index = index + 2 ^ (6 - position)
            end
        end
        return BASE64_ALPHABET:sub(index + 1, index + 1)
    end) .. ({ "", "==", "=" })[#value % 3 + 1])
end

local function bridge_request(body, wait_for_response, timeout_seconds)
    request_counter = request_counter + 1
    local request_id = string.format(
        "%d-%d-%d",
        os.time(),
        math.floor(os.clock() * 1000000),
        request_counter
    )
    local temporary_path = BRIDGE_ROOT .. "\\" .. request_id .. ".request.tmp"
    local request_path = BRIDGE_ROOT .. "\\" .. request_id .. ".request"
    local response_path = BRIDGE_ROOT .. "\\" .. request_id .. ".response"

    local request_file = io.open(temporary_path, "wb")
    if not request_file then
        return nil
    end
    request_file:write(body)
    request_file:close()
    if not os.rename(temporary_path, request_path) then
        os.remove(temporary_path)
        return nil
    end

    if not wait_for_response then
        return true
    end

    local deadline = os.clock() + timeout_seconds
    repeat
        local response_file = io.open(response_path, "rb")
        if response_file then
            local response = response_file:read("*a")
            response_file:close()
            os.remove(response_path)
            return response ~= "" and response or nil
        end
    until os.clock() >= deadline

    os.remove(request_path)
    os.remove(response_path)
    return nil
end

local function request_context(prompt_b64)
    local body = string.format(
        [[{"operation":"context","prompt_b64":"%s","pinyin_constraints":[]}]],
        prompt_b64
    )
    return bridge_request(body, false, 0)
end

local function query_backend(input, prompt_b64, candidate_count, timeout_seconds)
    local body = string.format(
        [[{"operation":"query","prompt_b64":"%s","pinyin_constraints":["%s"],"candidate_count":%d}]],
        prompt_b64,
        input:lower(),
        candidate_count
    )
    return bridge_request(body, true, timeout_seconds)
end

function M.init(env)
    env.neural_history = {}
    env.neural_history_bytes = 0
    env.neural_prompt_b64 = ""
    request_context(env.neural_prompt_b64)
    env.neural_commit_notifier = env.engine.context.commit_notifier:connect(function(context)
        local committed = context:get_commit_text()
        if not committed or committed == "" then
            return
        end
        local previous = env.neural_history[#env.neural_history]
        local separator = ""
        if previous
            and previous:match("[A-Za-z0-9]$")
            and committed:match("^[A-Za-z0-9]")
        then
            separator = " "
        end
        local contextual_commit = separator .. committed
        table.insert(env.neural_history, contextual_commit)
        env.neural_history_bytes = env.neural_history_bytes + #contextual_commit
        while env.neural_history_bytes > MAX_CONTEXT_BYTES and #env.neural_history > 1 do
            local removed = table.remove(env.neural_history, 1)
            env.neural_history_bytes = env.neural_history_bytes - #removed
        end
        env.neural_prompt_b64 = base64_encode(table.concat(env.neural_history))
        request_context(env.neural_prompt_b64)
    end)
end

function M.func(input, seg, env)
    if not seg:has_tag("abc") or not is_safe_pinyin(input) then
        return
    end

    local responses = query_backend(
        input,
        env.neural_prompt_b64,
        MAX_CANDIDATES,
        FIRST_PAGE_TIMEOUT_SECONDS
    )
    if not responses then
        return
    end

    local emitted = 0
    local seen = {}
    for line in responses:gmatch("[^\r\n]+") do
        if emitted >= MAX_CANDIDATES then break end
        local consumed, text = line:match("^(%d+)\t(.+)$")
        consumed = tonumber(consumed)
        if consumed and consumed > 0 and consumed <= #input and text and text ~= "" then
            local key = tostring(consumed) .. "\t" .. text
            if not seen[key] then
                seen[key] = true
                local candidate = Candidate("neural", seg.start, math.min(seg._end, seg.start + consumed), text, " Neural")
                candidate.quality = 100000 - emitted
                yield(candidate)
                emitted = emitted + 1
            end
        end
    end
end

function M.fini(env)
    if env.neural_commit_notifier then
        env.neural_commit_notifier:disconnect()
    end
end

return M
