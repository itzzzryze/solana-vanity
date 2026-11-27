/* Shared OpenCL 1.2 and CUDA SHA-256 search kernel. */
#ifdef CUDA_BUILD
#define DEVICE __device__ __forceinline__
#define KERNEL extern "C" __global__
#define GLOBAL
#define CONSTANT static constexpr
#define ITEM (blockIdx.x * blockDim.x + threadIdx.x)
#define ITEMS (gridDim.x * blockDim.x)
#define INCREMENT(ptr) atomicAdd(ptr, 1u)
#define FIRST_BIT(bits) (__ffs(bits) - 1)
#define clz(value) __clz(value)
typedef unsigned long long u64;
#else
#define DEVICE static inline
#define KERNEL __kernel
#define GLOBAL __global
#define CONSTANT __constant
#define ITEM ((uint)get_global_id(0))
#define ITEMS ((uint)get_global_size(0))
#define INCREMENT(ptr) atomic_inc(ptr)
#define FIRST_BIT(bits) (31 - clz((bits) & -(bits)))
typedef ulong u64;
#endif

typedef unsigned int u32;
typedef unsigned char u8;
typedef struct { u32 value[8]; } Words;

#define SHA_CONSTANTS \
  0x428a2f98u,0x71374491u,0xb5c0fbcfu,0xe9b5dba5u,0x3956c25bu,0x59f111f1u,0x923f82a4u,0xab1c5ed5u, \
  0xd807aa98u,0x12835b01u,0x243185beu,0x550c7dc3u,0x72be5d74u,0x80deb1feu,0x9bdc06a7u,0xc19bf174u, \
  0xe49b69c1u,0xefbe4786u,0x0fc19dc6u,0x240ca1ccu,0x2de92c6fu,0x4a7484aau,0x5cb0a9dcu,0x76f988dau, \
  0x983e5152u,0xa831c66du,0xb00327c8u,0xbf597fc7u,0xc6e00bf3u,0xd5a79147u,0x06ca6351u,0x14292967u, \
  0x27b70a85u,0x2e1b2138u,0x4d2c6dfcu,0x53380d13u,0x650a7354u,0x766a0abbu,0x81c2c92eu,0x92722c85u, \
  0xa2bfe8a1u,0xa81a664bu,0xc24b8b70u,0xc76c51a3u,0xd192e819u,0xd6990624u,0xf40e3585u,0x106aa070u, \
  0x19a4c116u,0x1e376c08u,0x2748774cu,0x34b0bcb5u,0x391c0cb3u,0x4ed8aa4au,0x5b9cca4fu,0x682e6ff3u, \
  0x748f82eeu,0x78a5636fu,0x84c87814u,0x8cc70208u,0x90befffau,0xa4506cebu,0xbef9a3f7u,0xc67178f2u

CONSTANT u32 constants[64] = { SHA_CONSTANTS };

DEVICE u32 rotate_right(u32 v, unsigned n) { return (v >> n) | (v << (32u - n)); }
DEVICE u32 small0(u32 v) { return rotate_right(v,7) ^ rotate_right(v,18) ^ (v >> 3); }
DEVICE u32 small1(u32 v) { return rotate_right(v,17) ^ rotate_right(v,19) ^ (v >> 10); }
DEVICE u32 big0(u32 v) { return rotate_right(v,2) ^ rotate_right(v,13) ^ rotate_right(v,22); }
DEVICE u32 big1(u32 v) { return rotate_right(v,6) ^ rotate_right(v,11) ^ rotate_right(v,25); }

DEVICE Words compress(Words input, u32 words[16]) {
    u32 a=input.value[0], b=input.value[1], c=input.value[2], d=input.value[3];
    u32 e=input.value[4], f=input.value[5], g=input.value[6], h=input.value[7];
    #pragma unroll
    for (int i=0; i<64; ++i) {
        const int j=i&15;
        if (i>=16) words[j] += small0(words[(j+1)&15]) + words[(j+9)&15]
                               + small1(words[(j+14)&15]);
        const u32 first=h+big1(e)+(g^(e&(f^g)))+constants[i]+words[j];
        const u32 second=big0(a)+((a&b)|(c&(a|b)));
        h=g; g=f; f=e; e=d+first; d=c; c=b; b=a; a=first+second;
    }
    Words result = {{input.value[0]+a,input.value[1]+b,input.value[2]+c,input.value[3]+d,
             input.value[4]+e,input.value[5]+f,input.value[6]+g,input.value[7]+h}};
    return result;
}

DEVICE u32 hex_word(u64 n) {
    u32 output=0;
    #pragma unroll
    for (int i=12;i>=0;i-=4) {
        const u32 digit=(n>>i)&15;
        output=(output<<8) | (digit + (digit < 10 ? '0' : 'a'-10));
    }
    return output;
}

DEVICE Words derive(Words base, Words tags, u64 counter) {
    u32 block[16];
    #pragma unroll
    for (int i=0;i<8;++i) block[i]=base.value[i];
    #pragma unroll
    for (int i=0;i<4;++i) block[8+i]=tags.value[i];
    #pragma unroll
    for (int i=0;i<4;++i) block[12+i]=hex_word(counter>>((3-i)*16));
    const Words iv={{0x6a09e667u,0xbb67ae85u,0x3c6ef372u,0xa54ff53au,
                     0x510e527fu,0x9b05688cu,0x1f83d9abu,0x5be0cd19u}};
    const Words state=compress(iv,block);
    u32 padding[16]={0,0,0,0,0,0,0,0,0x80000000u,0,0,0,0,0,0,768u};
    return compress(state,padding);
}

#define BASE58_CHUNK 656356768u
#define RESULT_WIDTH 11u
#define PATTERN_WIDTH 92u

DEVICE u32 tail_digits(Words digest) {
    u64 rest = 0;
    #pragma unroll
    for (int i = 0; i < 8; ++i)
        rest = ((rest << 32) | digest.value[i]) % BASE58_CHUNK;
    return (u32)rest;
}

DEVICE int encode_reversed(Words digest, u8 digits[44]) {
    int zeros = 0;
    for (int i = 0; i < 8; ++i) {
        const u32 word = digest.value[i];
        if (!word) {
            zeros += 4;
        } else {
            zeros += clz(word) / 8;
            break;
        }
    }

    int length = 0;
    for (int first = zeros / 4; first < 8;) {
        u64 rest = 0;
        for (int i = first; i < 8; ++i) {
            const u64 combined = (rest << 32) | digest.value[i];
            digest.value[i] = (u32)(combined / BASE58_CHUNK);
            rest = combined % BASE58_CHUNK;
        }
        while (first < 8 && !digest.value[first]) ++first;
        for (int i = 0; i < 5 && (rest || first < 8); ++i) {
            digits[length++] = (u8)(rest % 58);
            rest /= 58;
        }
    }
    for (int i = 0; i < zeros; ++i) digits[length++] = 0;
    return length;
}

DEVICE int accepts(GLOBAL const u32 *masks, u32 digit) {
    return (masks[digit >> 5] >> (digit & 31)) & 1u;
}

DEVICE int matches(GLOBAL const u32 *pattern, const u8 digits[44], int length) {
    const int left = (int)pattern[0], right = (int)pattern[1];
    if (length < left + right || (pattern[2] && length != left)) return 0;
    for (int i = 0; i < left; ++i)
        if (!accepts(pattern + 4 + i * 2, digits[length - i - 1])) return 0;
    for (int i = 0; i < right; ++i)
        if (!accepts(pattern + 4 + (left + i) * 2, digits[i])) return 0;
    return 1;
}

KERNEL void scan(GLOBAL const u32 *data, GLOBAL const u32 *buckets,
                 GLOBAL const u32 *patterns, GLOBAL const u32 *active,
                 GLOBAL u32 *out, u64 start, u32 size, u32 groups) {
    Words base, tag;
    #pragma unroll
    for (int i = 0; i < 8; ++i) base.value[i] = data[i];
    #pragma unroll
    for (int i = 0; i < 4; ++i) tag.value[i] = data[8+i];
    for (u32 offset = ITEM; offset < size; offset += ITEMS) {
        const Words digest = derive(base, tag, start + offset);
        GLOBAL const u32 *bucket = buckets + (digest.value[0] >> 16) * groups;
        u8 digits[44];
        int length = 0;
        u32 tail = 0xffffffffu;
        for (u32 group = 0; group < groups; ++group) {
            u32 flags = bucket[group] & active[group];
            while (flags) {
                const u32 index = group * 32 + FIRST_BIT(flags);
                flags &= flags - 1;
                GLOBAL const u32 *pattern = patterns + index * PATTERN_WIDTH;
                const u32 right = pattern[1];
                if (right) {
                    if (tail == 0xffffffffu) tail = tail_digits(digest);
                    u32 rest = tail;
                    int accepted = 1;
                    for (u32 i = 0; i < right && i < 5; ++i) {
                        if (!accepts(pattern + 4 + (pattern[0] + i) * 2, rest % 58)) {
                            accepted = 0;
                            break;
                        }
                        rest /= 58;
                    }
                    if (!accepted) continue;
                }
                if (!length) length = encode_reversed(digest, digits);
                if (matches(pattern, digits, length)) {
                    GLOBAL u32 *entry = out + index * RESULT_WIDTH;
                    const u32 slot = INCREMENT(entry);
                    if (slot < RESULT_WIDTH - 1) entry[1 + slot] = offset;
                }
            }
        }
    }
}
